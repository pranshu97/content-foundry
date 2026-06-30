"""Image provider protocol + OpenAI/Stability adapters (Ch. 11.5). SDKs imported lazily."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tenacity import retry, stop_after_attempt, wait_exponential


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
