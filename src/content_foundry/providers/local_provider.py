"""Local / self-hosted LLM adapter (cost saver) — SDK imported lazily (Ch. 3.4).

Talks to any OpenAI-compatible chat-completions server, so a single adapter covers Ollama,
LM Studio, vLLM, llama.cpp's server, LocalAI, etc. The only difference from the hosted OpenAI
adapter is the ``base_url`` pointing at the local server.

All calls use the configured ``local_llm_model``: the per-call ``model`` override (cloud model
names produced by tiering / the agents) is intentionally ignored, because a local server only
serves the model(s) it has loaded. This makes "run everything locally" work by setting just
``LOCAL_LLM_BASE_URL`` + ``LOCAL_LLM_MODEL``.
"""

from __future__ import annotations

from tenacity import retry, stop_after_attempt, wait_exponential

from ..errors import LLMError
from .base import LLMResponse, run_interruptible


class LocalLLMProvider:
    name = "local"

    def __init__(self, base_url: str, model: str, api_key: str = "local") -> None:
        self._base_url = base_url
        self._model = model
        # OpenAI's SDK rejects an empty key even when the local server ignores it.
        self._api_key = api_key or "local"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def _call(self, *, messages: list[dict], temperature: float, max_tokens: int):
        import openai  # lazy

        client = openai.OpenAI(api_key=self._api_key, base_url=self._base_url)
        return client.chat.completions.create(
            model=self._model,
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
        model: str | None = None,  # accepted for protocol parity; the local model is always used
    ) -> LLMResponse:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = run_interruptible(
                lambda: self._call(
                    messages=messages, temperature=temperature, max_tokens=max_tokens
                )
            )
        except Exception as exc:
            raise LLMError(f"Local LLM call failed ({self._base_url}): {exc}") from exc

        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=choice.message.content or "",
            model=self._model,
            provider=self.name,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
