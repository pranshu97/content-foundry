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


def _raise_for_image_status(resp) -> None:
    """Raise a non-retryable ``_ImageClientError`` on any 4xx (won't recover), else raise on 5xx."""
    if 400 <= resp.status_code < 500:
        raise _ImageClientError(f"image API {resp.status_code}: {resp.text[:200]}")
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
    model name, so one adapter + one GOOGLE_API_KEY covers whichever you configure. Native REST via
    httpx (no extra SDK). Note: Imagen is deprecated (shuts down 2026-08-17) — gemini-2.5-flash-image
    (Nano Banana) is the durable choice."""

    name = "google"
    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
        self, api_key: str, model: str = "gemini-2.5-flash-image", aspect_ratio: str = "16:9"
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._aspect = aspect_ratio

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
        retry=retry_if_not_exception_type(_ImageClientError),  # a 4xx won't recover; hand off fast
    )
    def generate(self, prompt: str, size: str = "1024x1024") -> bytes:
        import base64

        import httpx  # lazy-ish (core dep, kept local for symmetry)

        if self._model.startswith("imagen"):
            resp = httpx.post(
                f"{self._BASE_URL}/models/{self._model}:predict",
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
            f"{self._BASE_URL}/models/{self._model}:generateContent",
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
