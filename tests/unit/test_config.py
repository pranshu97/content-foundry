"""Unit: Settings validators, parsing, secret redaction (Ch. 6)."""

from __future__ import annotations

import json

import pytest

from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.errors import ConfigError


def test_settings_singleton(settings):
    assert get_settings() is settings


def test_enabled_sources_parse(settings):
    assert settings.enabled_sources_list == ["adzuna", "layoffs", "news"]


def test_thumbnail_and_resolution_parse(settings):
    assert settings.thumbnail_wh == (1280, 720)
    assert settings.resolution_wh == (1920, 1080)


def test_content_format_long_is_the_default(settings):
    # Long-form default: every effective_* value mirrors the long-form field, so nothing changes.
    assert settings.is_short is False
    assert settings.effective_resolution == settings.video_resolution
    assert settings.resolution_wh == (1920, 1080)
    assert settings.effective_target_words == settings.script_target_words
    assert settings.effective_scenes == settings.scenes_per_video
    assert settings.effective_captions_enabled == settings.captions_enabled
    assert settings.effective_scene_transition == settings.scene_transition
    assert settings.effective_intro_enabled == settings.intro_enabled
    assert settings.effective_thumbnail_size == settings.thumbnail_size
    assert settings.effective_thumbnail_wh == (1280, 720)
    assert settings.effective_avatar_position == settings.avatar_position


def test_content_format_short_switches_effective_values(monkeypatch):
    monkeypatch.setenv("CONTENT_FORMAT", "short")
    reset_settings_cache()
    s = get_settings()
    assert s.is_short is True
    assert s.effective_resolution == "1080x1920"
    assert s.resolution_wh == (1080, 1920)  # vertical 9:16
    assert s.effective_target_words == s.shorts_target_words
    assert s.effective_scenes == s.shorts_scenes
    assert s.effective_captions_enabled is True  # Shorts burn captions by default
    assert s.effective_intro_enabled is False  # Shorts skip the fixed intro tagline
    assert s.effective_min_scenes == min(s.min_scenes, s.shorts_scenes)
    assert s.effective_thumbnail_size == "1080x1920"  # vertical thumbnail matches the frame
    assert s.effective_thumbnail_wh == (1080, 1920)
    assert s.effective_avatar_position == "top-right"  # Shorts pin the avatar top-right


def test_effective_avatar_scale_is_half_for_short(monkeypatch):
    reset_settings_cache()
    assert get_settings().effective_avatar_scale == get_settings().avatar_scale  # long: unchanged
    monkeypatch.setenv("CONTENT_FORMAT", "short")
    monkeypatch.setenv("AVATAR_SCALE", "0.15")
    reset_settings_cache()
    assert get_settings().effective_avatar_scale == 0.075  # half of 0.15


def test_bad_resolution_raises(monkeypatch):
    monkeypatch.setenv("SHORTS_RESOLUTION", "tall")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_bad_source_raises(monkeypatch):
    monkeypatch.setenv("ENABLED_SOURCES", "adzuna,bogus")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_adzuna_missing_keys_raises(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_fallback_requires_key(monkeypatch):
    monkeypatch.setenv("FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_local_provider_requires_base_url(monkeypatch):
    monkeypatch.setenv("PRIMARY_PROVIDER", "local")
    monkeypatch.setenv("FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_local_fallback_skips_cloud_key(monkeypatch):
    # A 'local' fallback must NOT require a cloud API key (it talks to a self-hosted server).
    monkeypatch.setenv("FALLBACK_PROVIDER", "local")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
    reset_settings_cache()
    assert get_settings().fallback_provider == "local"


def test_tts_edge_needs_no_key(monkeypatch):
    # Edge (free Microsoft neural TTS) needs no API key.
    monkeypatch.setenv("TTS_PROVIDER", "edge")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    reset_settings_cache()
    assert get_settings().tts_provider == "edge"


def test_image_api_key_is_separate_from_the_text_one(monkeypatch):
    """Image models need a BILLED Google project while text stays on the free tier, so the paid key
    must be reachable ONLY by image generation — the LLM chain keeps using google_api_key."""
    monkeypatch.setenv("GOOGLE_API_KEY", "free-tier-text-key")
    monkeypatch.setenv("GOOGLE_IMAGE_API_KEY", "paid-image-key")
    reset_settings_cache()
    s = get_settings()
    assert s.effective_google_image_api_key == "paid-image-key"
    assert s.google_api_key == "free-tier-text-key"  # text NEVER sees the billed key


def test_image_api_key_falls_back_to_the_shared_key(monkeypatch):
    # Blank = the original single-key behaviour, so existing setups are unchanged.
    monkeypatch.setenv("GOOGLE_API_KEY", "shared-key")
    monkeypatch.setenv("GOOGLE_IMAGE_API_KEY", "")
    reset_settings_cache()
    assert get_settings().effective_google_image_api_key == "shared-key"


def test_image_key_can_be_forced_to_the_free_one(monkeypatch):
    """`visuals --free-images` flips this so prompt iteration costs nothing even with a paid key set."""
    monkeypatch.setenv("GOOGLE_API_KEY", "free-tier-text-key")
    monkeypatch.setenv("GOOGLE_IMAGE_API_KEY", "paid-image-key")
    monkeypatch.setenv("GOOGLE_IMAGE_USE_PAID_KEY", "false")
    reset_settings_cache()
    assert get_settings().effective_google_image_api_key == "free-tier-text-key"


def test_pipeline_runs_remake_images_by_default(monkeypatch):
    # A full run rewrites the script, so images made for the old wording would be stale. Only the
    # standalone `visuals` command opts into keeping them.
    reset_settings_cache()
    assert get_settings().visuals_redo_images is True


def test_google_image_models_chain_is_best_first(monkeypatch):
    monkeypatch.setenv("GOOGLE_IMAGE_MODELS", "banana, banana-pro ,banana-2,")
    reset_settings_cache()
    # Order is preserved (best first), blanks dropped, whitespace stripped.
    assert get_settings().google_image_models_list == ["banana", "banana-pro", "banana-2"]


def test_google_image_models_defaults_to_the_single_model(monkeypatch):
    monkeypatch.setenv("GOOGLE_IMAGE_MODELS", "")
    monkeypatch.setenv("GOOGLE_IMAGE_MODEL", "gemini-2.5-flash-image")
    reset_settings_cache()
    assert get_settings().google_image_models_list == ["gemini-2.5-flash-image"]


def test_google_image_provider_requires_an_image_key(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    monkeypatch.setenv("GOOGLE_IMAGE_API_KEY", "")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_tts_piper_requires_model_path(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "piper")
    monkeypatch.setenv("PIPER_MODEL_PATH", "")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_disclosure_gate_blocks_auto_public(monkeypatch):
    monkeypatch.setenv("PUBLISH_MODE", "auto")
    monkeypatch.setenv("YOUTUBE_PRIVACY_STATUS", "public")
    monkeypatch.setenv("REQUIRE_MANUAL_DISCLOSURE_BEFORE_PUBLIC", "true")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_telegram_requires_creds(monkeypatch):
    monkeypatch.setenv("NOTIFY_ENABLED", "true")
    monkeypatch.setenv("NOTIFIER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_config_hash_redacts_secrets(settings):
    blob = json.dumps(settings.redacted_dict())
    assert "test-anthropic" not in blob
    assert settings.redacted_dict()["anthropic_api_key"] == "***"
    assert settings.config_hash.startswith("sha256:")


def test_credential_status(settings):
    status = settings.credential_status()
    assert status["anthropic_api_key"] == "set ✓"
    # client secrets *file* is a path, not a secret -> excluded from status
    assert "youtube_client_secrets_file" not in status
    # non-secret numeric/text settings must not be flagged as credentials
    assert "llm_max_tokens" not in status
    assert "telegram_bot_token" in status
