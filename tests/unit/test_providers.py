"""Unit: provider protocol helpers, FallbackProvider, and the config-driven factories."""

from __future__ import annotations

import pytest

from career_engine.errors import LLMError
from career_engine.providers import (
    FallbackProvider,
    build_broll_client,
    build_image_provider,
    build_llm_provider,
    build_publisher,
    build_render_backend,
    build_tts_provider,
)
from career_engine.providers.base import LLMResponse, extract_json


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
