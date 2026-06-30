"""FallbackProvider — try the primary LLM, then a secondary on failure (Ch. 3.4, 21.2)."""

from __future__ import annotations

from ..errors import LLMError
from .base import LLMProvider, LLMResponse


class FallbackProvider:
    """Wraps a primary provider and an optional secondary used only on primary failure."""

    name = "fallback"

    def __init__(self, primary: LLMProvider, secondary: LLMProvider | None = None) -> None:
        self.primary = primary
        self.secondary = secondary

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        try:
            return self.primary.complete(
                prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                model=model,
            )
        except LLMError:
            if self.secondary is None:
                raise
            return self.secondary.complete(
                prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                model=model,
            )
