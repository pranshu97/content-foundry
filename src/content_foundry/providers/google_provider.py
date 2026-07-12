"""Google AI Studio (Gemini) LLM adapter — native REST via httpx, no extra SDK (Ch. 3.4).

Talks to the Generative Language API (https://ai.google.dev). Each instance wraps ONE Gemini model;
the provider factory builds a best-first FallbackProvider chain from ``GOOGLE_MODELS`` so a model that
hits quota or errors hands off to the next. The per-call cloud model override produced by tiering is
intentionally ignored (exactly like the local adapter), so "run the whole pipeline on Google" is just
GOOGLE_API_KEY + GOOGLE_MODELS.

On an HTTP 429 (free-tier quota / rate limit) it raises :class:`LLMRateLimitError` WITHOUT retrying,
so :class:`FallbackProvider` can switch the rest of the run to the next model (or local) instead of
failing.
"""

from __future__ import annotations

from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from ..errors import LLMError, LLMRateLimitError
from .base import LLMResponse, run_interruptible


class GoogleProvider:
    name = "google"
    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
        self, api_key: str, model: str, *, top_p: float | None = None, thinking: bool = False
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._top_p = top_p
        self._thinking = thinking

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
        # A quota / rate-limit error won't recover on retry — fail fast so the fallback takes over.
        retry=retry_if_not_exception_type(LLMRateLimitError),
    )
    def _call(
        self, *, model: str, system: str | None, prompt: str, temperature: float, max_tokens: int
    ) -> dict:
        import httpx  # lazy

        # "Thinking" on: ask the model to reason first. Prepend the [THINK] marker to the system
        # prompt AND send a thinkingConfig (Gemini 2.5+/3.x). Gemma has no thinking mode, so skip the
        # config for it (the marker is harmless).
        is_gemma = "gemma" in model.lower()
        if self._thinking and system:
            system = f"[THINK]\n{system}"
        # Gemma models served through the Gemini API have no separate system role, so fold the system
        # text into the user turn for them; Gemini models use the native systemInstruction field.
        user_text = f"{system}\n\n{prompt}" if (system and is_gemma) else prompt
        gen_cfg: dict = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if self._top_p is not None:
            gen_cfg["topP"] = self._top_p
        if self._thinking and not is_gemma:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": -1}  # -1 = dynamic (the model sizes it)
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": gen_cfg,
        }
        if system and not is_gemma:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        resp = httpx.post(
            f"{self._BASE_URL}/models/{model}:generateContent",
            params={"key": self._api_key},
            json=body,
            timeout=120,
        )
        if resp.status_code == 429:  # quota / rate limit — do NOT retry; let the fallback take over
            raise LLMRateLimitError(
                f"Google model '{model}' rate-limited / quota exhausted (429): {resp.text[:200]}"
            )
        resp.raise_for_status()
        return resp.json()

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,  # accepted for protocol parity; this instance's own model is used
    ) -> LLMResponse:
        try:
            data = run_interruptible(
                lambda: self._call(
                    model=self._model,
                    system=system,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            )
        except LLMRateLimitError:
            raise  # keep the type so FallbackProvider latches to the fallback for the rest of the run
        except Exception as exc:  # network / SDK error after retries (a bad model id -> 404, etc.)
            raise LLMError(f"Google model '{self._model}' call failed: {exc}") from exc

        candidates = data.get("candidates") or []
        text = ""
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            model=self._model,
            provider=self.name,
            prompt_tokens=usage.get("promptTokenCount", 0) or 0,
            completion_tokens=usage.get("candidatesTokenCount", 0) or 0,
        )
