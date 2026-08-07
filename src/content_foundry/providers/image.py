"""Image provider protocol + OpenAI/Stability/Google/Pollinations adapters + a fallback wrapper
(Ch. 11.5). SDKs / HTTP clients are imported lazily inside methods.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from ..logging import get_logger


class _ImageClientError(Exception):
    """A 4xx from an image API (e.g. 400 paid-plan-required, 429 quota) — it will NOT recover on
    retry, so we fail fast and let a fallback image provider take over."""


_MAX_RAW_DETAIL = 600  # only for a body we cannot parse; the parsed summary below is far shorter
_LOG_DETAIL_CHARS = 500


def _error_detail(resp) -> str:
    """A COMPACT but genuinely diagnostic summary of an image API error.

    This used to be a blind ``resp.text[:200]``, which reliably cut the body off mid-sentence and
    threw away the one field that matters. Google reports WHICH quota was exhausted, and a
    ``-FreeTier`` quota id on a supposedly paid project is the difference between "rate limited,
    wait for the reset" and "billing is not active, this will never recover on its own". That
    distinction is invisible in the human-readable message (both say "check your plan and billing
    details"), so the quota ids are lifted to the FRONT and the free-tier case is named outright.
    """
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:_MAX_RAW_DETAIL]
    if not isinstance(body, dict):
        return resp.text[:_MAX_RAW_DETAIL]
    err = body.get("error") or {}

    quotas: list[str] = []
    retry_after = ""
    for det in err.get("details") or []:
        if not isinstance(det, dict):
            continue
        for violation in det.get("violations") or []:
            quota_id = (violation or {}).get("quotaId")
            if quota_id:
                quotas.append(str(quota_id))
        if det.get("retryDelay"):
            retry_after = str(det["retryDelay"])

    parts: list[str] = []
    if err.get("status"):
        parts.append(str(err["status"]))
    if quotas:
        unique = sorted(set(quotas))
        parts.append(f"quotas={','.join(unique)}")
        if all("FreeTier" in q for q in unique):
            parts.append(
                "SERVED ON THE FREE TIER (billing is NOT active for this project, so this will "
                "not clear by waiting - fix the billing account)"
            )
    if retry_after:
        parts.append(f"retry_after={retry_after}")
    message = " ".join(str(err.get("message") or "").split())
    if message:
        parts.append(message[:300])
    return " | ".join(parts) or resp.text[:_MAX_RAW_DETAIL]


def _raise_for_image_status(resp) -> None:
    """Raise a non-retryable ``_ImageClientError`` on any 4xx (won't recover), else raise on 5xx."""
    if 400 <= resp.status_code < 500:
        raise _ImageClientError(f"image API {resp.status_code}: {_error_detail(resp)}")
    resp.raise_for_status()


@runtime_checkable
class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, size: str = "1024x1024") -> bytes:
        """Return PNG bytes for the given prompt."""
        ...


class OpenAIImage:
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-image-1") -> None:
        self._api_key = api_key
        self._model = model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def generate(self, prompt: str, size: str = "1024x1024") -> bytes:
        import base64

        import openai  # lazy

        client = openai.OpenAI(api_key=self._api_key)
        resp = client.images.generate(model=self._model, prompt=prompt, size=size, n=1)
        return base64.b64decode(resp.data[0].b64_json)


class StabilityImage:
    name = "stability"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def generate(self, prompt: str, size: str = "1024x1024") -> bytes:
        import httpx  # lazy-ish (core dep, kept local for symmetry)

        resp = httpx.post(
            "https://api.stability.ai/v2beta/stable-image/generate/core",
            headers={"authorization": f"Bearer {self._api_key}", "accept": "image/*"},
            files={"none": ""},
            data={"prompt": prompt, "output_format": "png"},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.content


class GoogleImage:
    """Google AI Studio image generation. Supports BOTH Imagen (``imagen-*`` via the :predict endpoint)
    and Nano Banana / Gemini image models (``gemini-*-image`` via :generateContent), dispatched by the
    model name, so one adapter covers whichever you configure. Native REST via httpx (no extra SDK).

    Takes a BEST-FIRST list of models and tries them IN ORDER, moving to the next on ANY failure
    (quota/429, bad id/404, 5xx) — the same shape as the text chain. That way the best image model is
    always attempted first and running into its quota degrades to the next best one instead of
    dropping the whole run to the free fallback provider. Note: Imagen is deprecated (shuts down
    2026-08-17), so the Nano Banana / ``gemini-*-image`` family is the durable choice.
    """

    name = "google"
    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
        self,
        api_key: str,
        model: str | list[str] = "gemini-2.5-flash-image",
        aspect_ratio: str = "16:9",
    ) -> None:
        self._api_key = api_key
        self._models = [model] if isinstance(model, str) else [m for m in model if m]
        self._aspect = aspect_ratio
        self._log = get_logger(component="image", provider="google")

    @property
    def _model(self) -> str:
        """The best-first model (kept for callers/tests that inspect the configured model)."""
        return self._models[0] if self._models else ""

    def generate(self, prompt: str, size: str = "1024x1024") -> bytes:
        if not self._models:
            raise ValueError("No Google image model configured")
        last: Exception | None = None
        for model in self._models:
            try:
                return self._generate_with(model, prompt)
            except Exception as exc:  # quota/404/5xx -> try the next-best model
                last = exc
                self._log.warning(
                    "google_image_model_failed",
                    model=model,
                    error=type(exc).__name__,
                    detail=str(exc)[:_LOG_DETAIL_CHARS],
                )
        assert last is not None  # the loop ran at least once, so a failure was recorded
        raise last

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
        retry=retry_if_not_exception_type(_ImageClientError),  # a 4xx won't recover; hand off fast
    )
    def _generate_with(self, model: str, prompt: str) -> bytes:
        import base64

        import httpx  # lazy-ish (core dep, kept local for symmetry)

        if model.startswith("imagen"):
            resp = httpx.post(
                f"{self._BASE_URL}/models/{model}:predict",
                params={"key": self._api_key},
                json={
                    "instances": [{"prompt": prompt}],
                    "parameters": {"sampleCount": 1, "aspectRatio": self._aspect},
                },
                timeout=120,
            )
            _raise_for_image_status(resp)
            preds = resp.json().get("predictions") or []
            b64 = preds[0].get("bytesBase64Encoded", "") if preds else ""
            if not b64:
                raise ValueError("Imagen returned no image")
            return base64.b64decode(b64)

        # Nano Banana / Gemini image model: image arrives inline in a content part.
        resp = httpx.post(
            f"{self._BASE_URL}/models/{model}:generateContent",
            params={"key": self._api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["IMAGE"]},
            },
            timeout=120,
        )
        _raise_for_image_status(resp)
        parts = ((resp.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        for part in parts:
            b64 = (part.get("inlineData") or {}).get("data")
            if b64:
                return base64.b64decode(b64)
        raise ValueError("Gemini image model returned no image")


class PollinationsImage:
    """Free, no-key AI image generation via Pollinations.ai. The practical choice when a paid image
    API isn't available: Google's Imagen is paid-only and its Nano Banana image model is blocked on
    the free tier, while OpenAI/Stability both cost money. Fetches the rendered image over HTTP."""

    name = "pollinations"
    _BASE_URL = "https://image.pollinations.ai/prompt/"

    def __init__(self, model: str = "flux") -> None:
        self._model = model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def generate(self, prompt: str, size: str = "1280x720") -> bytes:
        import urllib.parse

        import httpx  # lazy-ish (core dep, kept local for symmetry)

        width, _, height = size.partition("x")
        resp = httpx.get(
            self._BASE_URL + urllib.parse.quote(prompt),
            params={
                "width": int(width or 1280),
                "height": int(height or 720),
                "nologo": "true",
                "model": self._model,
            },
            timeout=180,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.content


class FallbackImageProvider:
    """Try the primary image provider; on ANY failure, fall back to the secondary. Lets a
    high-quality but paid/limited primary (e.g. Imagen) sit in front of a free safety net
    (Pollinations): if the primary is unavailable (paid-plan, quota, outage) the fallback still
    produces a thumbnail."""

    def __init__(self, primary: ImageProvider, secondary: ImageProvider) -> None:
        self.primary = primary
        self.secondary = secondary
        self.name = getattr(primary, "name", "image")
        self._log = get_logger(component="image_fallback")

    def generate(self, prompt: str, size: str = "1024x1024") -> bytes:
        try:
            return self.primary.generate(prompt, size=size)
        except Exception as exc:  # any primary failure -> use the free fallback
            self._log.warning(
                "image_primary_failed_using_fallback",
                primary=getattr(self.primary, "name", "?"),
                fallback=getattr(self.secondary, "name", "?"),
                error=str(exc)[:200],
            )
            return self.secondary.generate(prompt, size=size)
