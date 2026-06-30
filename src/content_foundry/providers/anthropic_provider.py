"""Anthropic (Claude) LLM adapter — SDK imported lazily (Ch. 3.4)."""

from __future__ import annotations

from tenacity import retry, stop_after_attempt, wait_exponential

from ..errors import LLMError
from .base import LLMResponse


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def _call(self, *, model: str, system: str, prompt: str, temperature: float, max_tokens: int):
        import anthropic  # lazy

        client = anthropic.Anthropic(api_key=self._api_key)
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
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
        try:
            msg = self._call(
                model=target_model,
                system=system or "",
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:  # network / SDK error after retries
            raise LLMError(f"Anthropic call failed: {exc}") from exc

        text = "".join(
            getattr(block, "text", "") for block in msg.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(msg, "usage", None)
        return LLMResponse(
            text=text,
            model=target_model,
            provider=self.name,
            prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
            completion_tokens=getattr(usage, "output_tokens", 0) or 0,
        )
