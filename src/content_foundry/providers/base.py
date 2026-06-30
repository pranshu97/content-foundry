"""LLM provider protocol + shared helpers (Ch. 3.4).

Agents depend on the :class:`LLMProvider` protocol — never on a vendor SDK. Concrete adapters
import their SDKs lazily (inside methods) so the package imports cleanly without them installed.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class LLMResponse(BaseModel):
    text: str
    model: str
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


@runtime_checkable
class LLMProvider(Protocol):
    """One method, two impls (Anthropic/OpenAI) + a fallback wrapper."""

    name: str

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse: ...


def extract_json(text: str) -> str:
    """Best-effort extraction of a single JSON object from an LLM response.

    Strips ``` fences and slices between the first ``{`` and last ``}``.
    """
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]
    return t
