"""Unit: provider protocol helpers, FallbackProvider, and the config-driven factories."""

from __future__ import annotations

import pytest

from content_foundry.errors import LLMError
from content_foundry.providers import (
    FallbackProvider,
    build_broll_client,
    build_image_provider,
    build_llm_provider,
    build_publisher,
    build_render_backend,
    build_tts_provider,
)
from content_foundry.providers.base import LLMResponse, extract_json


class _OK:
    name = "ok"

    def __init__(self):
        self.calls = 0

    def complete(self, prompt, **kwargs):
        self.calls += 1
        return LLMResponse(text="ok", model="m", provider="ok")


class _Boom:
    name = "boom"

    def complete(self, prompt, **kwargs):
        raise LLMError("down")


def test_extract_json_strips_fences():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('prefix {"a": 1} suffix') == '{"a": 1}'


def test_fallback_uses_primary():
    primary = _OK()
    fb = FallbackProvider(primary, _OK())
    assert fb.complete("x").provider == "ok"
    assert primary.calls == 1


def test_fallback_switches_on_error():
    secondary = _OK()
    fb = FallbackProvider(_Boom(), secondary)
    assert fb.complete("x").provider == "ok"
    assert secondary.calls == 1


def test_fallback_reraises_without_secondary():
    fb = FallbackProvider(_Boom(), None)
    with pytest.raises(LLMError):
        fb.complete("x")


def test_factories_build_expected_types(settings):
    llm = build_llm_provider(settings)
    assert isinstance(llm, FallbackProvider)
    assert llm.primary.name == "anthropic"
    assert llm.secondary.name == "openai"

    assert build_tts_provider(settings).name == "elevenlabs"
    assert build_image_provider(settings) is None  # IMAGE_PROVIDER=none in test env
    assert build_broll_client(settings).enabled is False  # no PEXELS key
    assert build_render_backend(settings).name == "ffmpeg"
    assert build_publisher(settings, dry_run=True).name == "dryrun"


def _fake_openai(captured: dict, *, content: str = "hello", boom: bool = False):
    """A stand-in ``openai`` module exposing the OpenAI-compatible chat API the local server uses."""
    import types

    mod = types.ModuleType("openai")

    def _client(api_key, base_url):
        captured["api_key"] = api_key
        captured["base_url"] = base_url

        def _create(*, model, messages, temperature, max_tokens):
            captured["model"] = model
            captured["messages"] = messages
            if boom:
                raise RuntimeError("server down")
            choice = types.SimpleNamespace(message=types.SimpleNamespace(content=content))
            usage = types.SimpleNamespace(prompt_tokens=5, completion_tokens=7)
            return types.SimpleNamespace(choices=[choice], usage=usage)

        completions = types.SimpleNamespace(create=_create)
        return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))

    mod.OpenAI = _client
    return mod


def test_local_provider_uses_local_model_and_base_url(monkeypatch):
    import sys

    from content_foundry.providers.local_provider import LocalLLMProvider

    captured: dict = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai(captured))
    provider = LocalLLMProvider("http://localhost:11434/v1", "llama3.1", api_key="local")
    resp = provider.complete("hi", system="sys", model="claude-sonnet-4-cloud")

    assert resp.provider == "local"
    assert resp.text == "hello"
    assert resp.model == "llama3.1"  # cloud per-call override is ignored
    assert captured["model"] == "llama3.1"
    assert captured["base_url"] == "http://localhost:11434/v1"
    assert captured["messages"][0] == {"role": "system", "content": "sys"}
    assert resp.prompt_tokens == 5 and resp.completion_tokens == 7


def test_local_provider_wraps_errors(monkeypatch):
    import sys

    from content_foundry.providers.local_provider import LocalLLMProvider

    monkeypatch.setitem(sys.modules, "openai", _fake_openai({}, boom=True))
    provider = LocalLLMProvider("http://localhost:11434/v1", "llama3.1")
    with pytest.raises(LLMError):
        provider.complete("hi")


def test_local_provider_factory(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("PRIMARY_PROVIDER", "local")
    monkeypatch.setenv("FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "phi3")
    reset_settings_cache()
    settings = get_settings()

    llm = build_llm_provider(settings)
    assert llm.primary.name == "local"
    assert llm.secondary is None
