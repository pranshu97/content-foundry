"""OpenAI LLM adapter — SDK imported lazily (Ch. 3.4)."""

from __future__ import annotations

from tenacity import retry, stop_after_attempt, wait_exponential

from ..errors import LLMError
from .base import LLMResponse, run_interruptible


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def _call(self, *, model: str, messages: list[dict], temperature: float, max_tokens: int):
        import openai  # lazy

        client = openai.OpenAI(api_key=self._api_key)
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        target_model = model or self._model
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = run_interruptible(
                lambda: self._call(
                    model=target_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            )
        except Exception as exc:
            raise LLMError(f"OpenAI call failed: {exc}") from exc

        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=choice.message.content or "",
            model=target_model,
            provider=self.name,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
