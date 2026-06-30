"""Unit: Settings validators, parsing, secret redaction (Ch. 6)."""

from __future__ import annotations

import json

import pytest

from career_engine.config import get_settings, reset_settings_cache
from career_engine.errors import ConfigError


def test_settings_singleton(settings):
    assert get_settings() is settings


def test_enabled_sources_parse(settings):
    assert settings.enabled_sources_list == ["adzuna", "layoffs", "news"]


def test_thumbnail_and_resolution_parse(settings):
    assert settings.thumbnail_wh == (1280, 720)
    assert settings.resolution_wh == (1920, 1080)


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
