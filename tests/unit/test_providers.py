"""Unit: provider protocol helpers, FallbackProvider, and the config-driven factories."""

from __future__ import annotations

import httpx
import pytest
import respx

from content_foundry.errors import LLMError, LLMRateLimitError
from content_foundry.providers import (
    FallbackProvider,
    build_broll_client,
    build_image_provider,
    build_llm_provider,
    build_publisher,
    build_render_backend,
    build_tts_provider,
)
from content_foundry.providers.base import (
    LLMResponse,
    _raise_in_thread,
    extract_json,
    run_interruptible,
)


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


class _BoomCounted:
    name = "boom"

    def __init__(self):
        self.calls = 0

    def complete(self, prompt, **kwargs):
        self.calls += 1
        raise LLMError("down")


class _RateLimited:
    name = "rate"

    def __init__(self):
        self.calls = 0

    def complete(self, prompt, **kwargs):
        self.calls += 1
        raise LLMRateLimitError("429 quota exhausted")


def test_extract_json_strips_fences():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('prefix {"a": 1} suffix') == '{"a": 1}'


def test_run_interruptible_returns_value():
    # Transparent for the fast path: the worker's return value is passed straight through.
    assert run_interruptible(lambda: 42) == 42


def test_run_interruptible_reraises_worker_error():
    # A failure inside the worker thread surfaces on the caller's thread unchanged.
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        run_interruptible(boom)


def test_raise_in_thread_retires_a_running_worker():
    # On Ctrl+C the main wait re-raises immediately AND pokes the daemon so it unwinds the moment its
    # blocking section yields, rather than running to completion in the background.
    import threading

    ev = threading.Event()
    entered = threading.Event()

    def loop():
        entered.set()
        try:
            while True:
                ev.wait(0.01)  # returns ~100x/s -> the async exception lands at the next bytecode
        except BaseException:
            return  # unwind cleanly, exactly like run_interruptible's worker does

    worker = threading.Thread(target=loop, daemon=True)
    worker.start()
    assert entered.wait(2.0)  # worker is in its wait loop
    _raise_in_thread(worker, SystemExit)
    worker.join(timeout=2.0)
    assert not worker.is_alive()


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


def test_fallback_latches_to_secondary_after_rate_limit():
    # Once the primary hits a 429, the WHOLE run switches to the secondary (primary not retried again).
    primary = _RateLimited()
    secondary = _OK()
    fb = FallbackProvider(primary, secondary)
    assert fb.complete("a").provider == "ok"  # primary 429 -> served by secondary AND latched off
    assert fb.complete("b").provider == "ok"  # primary skipped entirely now
    assert primary.calls == 1  # tried once, then disabled for the rest of the run
    assert secondary.calls == 2


def test_fallback_does_not_latch_on_transient_error():
    # A generic (non-rate-limit) error falls back for THAT call but keeps trying the primary next time.
    primary = _BoomCounted()
    secondary = _OK()
    fb = FallbackProvider(primary, secondary)
    fb.complete("a")
    fb.complete("b")
    assert primary.calls == 2  # retried each call (NOT latched)
    assert secondary.calls == 2


@respx.mock
def test_google_provider_parses_and_reports_usage():
    import json

    route = respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "hello "}, {"text": "world"}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 7},
        })
    )
    from content_foundry.providers.google_provider import GoogleProvider

    resp = GoogleProvider("key", "gemini-2.5-flash").complete("hi", system="sys")
    assert resp.text == "hello world"
    assert resp.provider == "google" and resp.model == "gemini-2.5-flash"
    assert resp.prompt_tokens == 5 and resp.completion_tokens == 7
    body = json.loads(route.calls.last.request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"  # Gemini uses the native system role


@respx.mock
def test_google_provider_raises_rate_limit_on_429():
    respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
    )
    from content_foundry.providers.google_provider import GoogleProvider

    with pytest.raises(LLMRateLimitError):
        GoogleProvider("key", "gemini-2.5-flash").complete("hi")


@respx.mock
def test_google_provider_folds_system_into_prompt_for_gemma():
    import json

    route = respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        )
    )
    from content_foundry.providers.google_provider import GoogleProvider

    GoogleProvider("key", "gemma-4-31b-it").complete("do it", system="be terse")
    body = json.loads(route.calls.last.request.content)
    assert "systemInstruction" not in body  # Gemma has no separate system role
    assert body["contents"][0]["parts"][0]["text"] == "be terse\n\ndo it"  # folded into the prompt


@respx.mock
def test_google_provider_sends_top_p_and_thinking():
    import json

    route = respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    )
    from content_foundry.providers.google_provider import GoogleProvider

    GoogleProvider("key", "gemini-2.5-flash", top_p=0.95, thinking=True).complete("hi", system="sys")
    body = json.loads(route.calls.last.request.content)
    assert body["generationConfig"]["topP"] == 0.95
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": -1}  # dynamic thinking
    assert body["systemInstruction"]["parts"][0]["text"] == "[THINK]\nsys"  # marker prepended


@respx.mock
def test_google_provider_skips_thinking_config_for_gemma():
    import json

    route = respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    )
    from content_foundry.providers.google_provider import GoogleProvider

    GoogleProvider("key", "gemma-4-31b-it", thinking=True).complete("do it", system="be terse")
    body = json.loads(route.calls.last.request.content)
    assert "thinkingConfig" not in body["generationConfig"]  # gemma has no thinking mode
    assert body["contents"][0]["parts"][0]["text"].startswith("[THINK]\nbe terse")  # marker folded in


def test_build_llm_provider_google_primary_local_fallback(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("PRIMARY_PROVIDER", "google")
    monkeypatch.setenv("FALLBACK_PROVIDER", "local")
    monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
    monkeypatch.setenv("GOOGLE_MODELS", "gemini-2.5-flash")  # a single Google model in this case
    reset_settings_cache()
    llm = build_llm_provider(get_settings())
    assert llm.primary.name == "google"  # switch to Google
    assert llm.secondary.name == "local"  # ...with local as the quota fallback


def test_build_llm_provider_google_two_model_chain(monkeypatch):
    # "Within Google": a best-first list, each model taking over on any error, then the outer (local).
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("PRIMARY_PROVIDER", "google")
    monkeypatch.setenv("FALLBACK_PROVIDER", "local")
    monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
    monkeypatch.setenv("GOOGLE_MODELS", "gemini-3.5-flash, gemini-2.5-flash, gemini-2.5-flash-lite")
    reset_settings_cache()
    llm = build_llm_provider(get_settings())
    assert llm.primary.name == "fallback"  # the intra-Google best-first chain
    assert llm.primary.primary.name == "google" and llm.primary.primary._model == "gemini-3.5-flash"
    # the next Google model takes over on ANY error, before the outer (local) fallback
    assert llm.primary.secondary.name == "fallback"  # nested: 2.5-flash -> 2.5-flash-lite
    assert llm.primary.secondary.primary._model == "gemini-2.5-flash"
    assert llm.primary.secondary.secondary._model == "gemini-2.5-flash-lite"
    assert llm.secondary.name == "local"  # ... then local as the final fallback


@respx.mock
def test_google_image_imagen_predict_path():
    import base64

    img = base64.b64encode(b"IMGBYTES").decode()
    respx.post(url__startswith="https://generativelanguage.googleapis.com/v1beta/models/imagen").mock(
        return_value=httpx.Response(200, json={"predictions": [{"bytesBase64Encoded": img}]})
    )
    from content_foundry.providers.image import GoogleImage

    out = GoogleImage("key", "imagen-4.0-ultra-generate-001").generate("a shocked developer")
    assert out == b"IMGBYTES"


@respx.mock
def test_google_image_nano_banana_generate_content_path():
    import base64

    img = base64.b64encode(b"NANO").decode()
    respx.post(url__startswith="https://generativelanguage.googleapis.com/v1beta/models/gemini").mock(
        return_value=httpx.Response(200, json={
            "candidates": [{"content": {"parts": [
                {"text": "here you go"},
                {"inlineData": {"mimeType": "image/png", "data": img}},
            ]}}]
        })
    )
    from content_foundry.providers.image import GoogleImage

    out = GoogleImage("key", "gemini-2.5-flash-image").generate("a shocked developer")
    assert out == b"NANO"


def test_build_image_provider_google(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("IMAGE_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
    monkeypatch.setenv("GOOGLE_IMAGE_MODEL", "imagen-4.0-ultra-generate-001")
    reset_settings_cache()
    provider = build_image_provider(get_settings())
    assert provider.name == "google" and provider._model == "imagen-4.0-ultra-generate-001"


@respx.mock
def test_pollinations_image_fetches_bytes():
    respx.get(url__startswith="https://image.pollinations.ai/prompt/").mock(
        return_value=httpx.Response(
            200, content=b"JPEGBYTES", headers={"content-type": "image/jpeg"}
        )
    )
    from content_foundry.providers.image import PollinationsImage

    assert PollinationsImage().generate("a shocked developer", size="1280x720") == b"JPEGBYTES"


def test_build_image_provider_pollinations(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("IMAGE_PROVIDER", "pollinations")  # free, no key required
    reset_settings_cache()
    assert build_image_provider(get_settings()).name == "pollinations"


@respx.mock
def test_google_image_fast_fails_on_client_error_no_retry():
    route = respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(400, json={"error": {"message": "Imagen is paid-only"}})
    )
    from content_foundry.providers.image import GoogleImage, _ImageClientError

    with pytest.raises(_ImageClientError):
        GoogleImage("key", "imagen-4.0-fast-generate-001").generate("x")
    assert route.call_count == 1  # a 4xx is NOT retried, so the fallback kicks in immediately


def test_fallback_image_provider_falls_back_on_primary_failure():
    from content_foundry.providers.image import FallbackImageProvider

    class _BoomImg:
        name = "boom"

        def generate(self, prompt, size="1024x1024"):
            raise RuntimeError("primary down")

    class _OKImg:
        name = "pollinations"

        def __init__(self):
            self.calls = 0

        def generate(self, prompt, size="1024x1024"):
            self.calls += 1
            return b"FALLBACK"

    ok = _OKImg()
    fb = FallbackImageProvider(_BoomImg(), ok)
    assert fb.generate("x") == b"FALLBACK"
    assert ok.calls == 1
    assert fb.name == "boom"  # reports the primary's (intended) name for metadata


def test_build_image_provider_google_primary_pollinations_fallback(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache
    from content_foundry.providers.image import FallbackImageProvider

    monkeypatch.setenv("IMAGE_PROVIDER", "google")
    monkeypatch.setenv("IMAGE_FALLBACK_PROVIDER", "pollinations")
    monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
    reset_settings_cache()
    provider = build_image_provider(get_settings())
    assert isinstance(provider, FallbackImageProvider)
    assert provider.primary.name == "google" and provider.secondary.name == "pollinations"


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


def test_tts_factory_builds_edge_and_piper(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("TTS_PROVIDER", "edge")
    reset_settings_cache()
    assert build_tts_provider(get_settings()).name == "edge"

    monkeypatch.setenv("TTS_PROVIDER", "piper")
    monkeypatch.setenv("PIPER_MODEL_PATH", "voices/en_US-amy.onnx")
    reset_settings_cache()
    assert build_tts_provider(get_settings()).name == "piper"


def test_tts_factory_builds_chatterbox(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("TTS_PROVIDER", "chatterbox")
    monkeypatch.setenv("TTS_REFERENCE_CLIP", "assets/voice_reference.wav")
    reset_settings_cache()
    tts = build_tts_provider(get_settings())
    assert tts.name == "chatterbox"
    assert tts.voice == "voice_reference"  # derived from the reference clip's file name


def test_chatterbox_missing_reference_raises():
    # The reference-clip guard fires BEFORE the model loads, so this needs no chatterbox install.
    from content_foundry.errors import TTSError
    from content_foundry.providers.tts import ChatterboxTTS

    with pytest.raises(TTSError):
        ChatterboxTTS("does_not_exist.wav").synthesize("hello")


def test_chunk_for_tts_splits_long_text_without_dropping_words():
    # Chatterbox truncates a single generate() past ~40s, so long scenes must be voiced in chunks.
    from content_foundry.providers.tts import _chunk_for_tts

    assert _chunk_for_tts("") == []
    assert _chunk_for_tts("Short line. Two sentences.") == ["Short line. Two sentences."]

    long = " ".join(f"This is sentence number {i}." for i in range(40))
    chunks = _chunk_for_tts(long, max_chars=100)
    assert len(chunks) > 1  # actually split
    assert all(len(c) <= 100 for c in chunks)  # each chunk fits the budget
    assert " ".join(chunks).split() == long.split()  # nothing truncated or duplicated

    # a single sentence longer than the budget still gets broken on word boundaries
    one_long = "word " * 60
    pieces = _chunk_for_tts(one_long.strip(), max_chars=50)
    assert len(pieces) > 1
    assert " ".join(pieces).split() == one_long.split()


def test_pick_voice_alternates_by_run_id_parity():
    from content_foundry.providers.tts import pick_voice

    assert pick_voice("0001", male="M", female="F", default="D") == "M"  # odd -> male
    assert pick_voice("0002", male="M", female="F", default="D") == "F"  # even -> female
    assert pick_voice("0007", male="M", female="F", default="D") == "M"
    assert pick_voice(None, male="M", female="F", default="D") == "D"  # no run id
    assert pick_voice("01KWZ9", male="M", female="F", default="D") == "D"  # legacy ULID -> default
    assert pick_voice("0003", male="", female="", default="D") == "D"  # unset -> default


def test_build_tts_provider_alternates_voice_by_run(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("TTS_PROVIDER", "edge")
    monkeypatch.setenv("TTS_VOICE_MALE", "en-US-GuyNeural")
    monkeypatch.setenv("TTS_VOICE_FEMALE", "en-US-AriaNeural")
    reset_settings_cache()
    s = get_settings()
    assert build_tts_provider(s, run_id="0001").voice == "en-US-GuyNeural"  # odd -> male
    assert build_tts_provider(s, run_id="0002").voice == "en-US-AriaNeural"  # even -> female
    assert build_tts_provider(s, run_id=None).voice == s.tts_voice_id  # no id -> default


def test_resolve_ffmpeg_prefers_configured_path(tmp_path):
    from content_foundry.providers.render_backend import resolve_ffmpeg

    fake = tmp_path / "ffmpeg.exe"
    fake.write_text("x")
    assert resolve_ffmpeg(str(fake)) == str(fake)


def test_resolve_ffmpeg_never_returns_missing_configured(tmp_path, monkeypatch):
    import content_foundry.providers.render_backend as rb

    monkeypatch.setattr(rb.shutil, "which", lambda _name: None)
    bad = str(tmp_path / "does-not-exist.exe")
    assert rb.resolve_ffmpeg(bad) != bad  # a non-existent path is skipped, never returned


def test_select_encoder_prefers_gpu_then_falls_back_to_cpu(monkeypatch):
    import content_foundry.providers.render_backend as rb

    monkeypatch.setattr(rb, "_available_encoders", lambda _exe: {"libx264", "h264_nvenc", "h264_amf"})
    # Simulate: NVENC listed but actually broken at runtime; AMF works.
    monkeypatch.setattr(rb, "_probe_encoder", lambda _exe, enc: enc == "h264_amf")
    rb._WORKING_ENCODER_CACHE.clear()
    assert rb._select_encoder("ffmpeg", "auto") == "h264_amf"  # skips broken NVENC, picks AMF
    rb._WORKING_ENCODER_CACHE.clear()
    monkeypatch.setattr(rb, "_probe_encoder", lambda _exe, _enc: False)  # all GPU broken -> CPU
    assert rb._select_encoder("ffmpeg", "auto") == "libx264"
    assert rb._select_encoder("ffmpeg", "h264_qsv") == "h264_qsv"  # an explicit choice always wins
    rb._WORKING_ENCODER_CACHE.clear()


def test_encoder_opts_per_family():
    import content_foundry.providers.render_backend as rb

    assert rb._encoder_opts("h264_nvenc")["rc"] == "vbr"
    assert rb._encoder_opts("h264_qsv")["global_quality"] == 23
    assert rb._encoder_opts("libx264") == {}  # CPU keeps ffmpeg defaults
