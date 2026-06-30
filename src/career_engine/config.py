"""Typed application configuration (Ch. 6).

A single :class:`Settings` object loads every value from environment variables / ``.env``.
No secret is ever hard-coded. Cross-field validators fail fast on inconsistent setups.

Accessed everywhere via :func:`get_settings` (an ``lru_cache``'d singleton — functionally the
"one settings object everyone shares" the spec calls for, but lazy so importing this module has
no side effects, which keeps tests hermetic).
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError

VALID_SOURCES = {"adzuna", "layoffs", "news", "bls"}
VALID_EVENTS = {
    "run_complete",
    "need_validation",
    "video_uploaded",
    "low_credits",
    "run_failed",
}
_SECRET_SUFFIXES = ("_key", "_token", "_secret", "_password")

# Suggested presets selectable via ``--profile`` (Ch. 6.5).
PROFILES: dict[str, dict[str, object]] = {
    "cheap": {"judge_mode": "deterministic", "image_provider": "none", "max_revisions": 1},
    "quality": {"judge_mode": "hybrid", "image_provider": "openai", "max_revisions": 3},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- LLM Providers ----------
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    primary_provider: Literal["anthropic", "openai"] = "anthropic"
    fallback_provider: Literal["anthropic", "openai", "none"] = "openai"
    generator_model: str = "claude-sonnet-4-20250514"
    judge_model: str = "claude-sonnet-4-20250514"
    llm_temperature: float = 0.7
    judge_temperature: float = 0.0
    llm_max_tokens: int = 4096

    # ---------- Data Sources ----------
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    newsapi_key: str = ""
    enabled_sources: str = "adzuna,layoffs,news"
    layoffs_feed_url: str = ""
    signal_cache_ttl_min: int = 720

    # ---------- Pipeline Behaviour ----------
    max_revisions: int = 3
    judge_mode: Literal["hybrid", "deterministic", "llm"] = "hybrid"
    pass_threshold: float = Field(7.5, ge=0, le=10)
    insight_min: float = Field(7.0, ge=0, le=10)
    grounding_min: float = Field(8.0, ge=0, le=10)
    fatigue_lookback: int = 5
    target_niche: str = "tech careers"
    script_target_words: int = 900
    min_facts: int = 3

    # ---------- Voiceover (TTS) ----------
    tts_provider: Literal["elevenlabs", "openai"] = "elevenlabs"
    elevenlabs_api_key: str = ""
    tts_voice_id: str = "Rachel"
    tts_model: str = "eleven_multilingual_v2"
    tts_format: str = "mp3_44100_128"

    # ---------- Visuals ----------
    image_provider: Literal["openai", "stability", "none"] = "openai"
    stability_api_key: str = ""
    pexels_api_key: str = ""
    visual_style: str = "clean infographic, high-contrast, bold text"
    scenes_per_video: int = 10
    thumbnail_size: str = "1280x720"

    # ---------- Render ----------
    render_backend: Literal["ffmpeg", "moviepy", "avatar"] = "ffmpeg"
    avatar_provider: Literal["none", "heygen", "did"] = "none"
    heygen_api_key: str = ""
    video_resolution: str = "1920x1080"
    video_fps: int = 30
    captions_enabled: bool = True
    caption_aligner: Literal["tts", "whisper"] = "tts"
    render_fallback: bool = True

    # ---------- Publishing (YouTube) ----------
    youtube_client_secrets_file: str = "secrets/client_secrets.json"
    youtube_token_file: str = "secrets/youtube_token.json"
    publish_mode: Literal["draft", "auto"] = "draft"
    youtube_privacy_status: Literal["private", "unlisted", "public"] = "private"
    youtube_category_id: str = "22"
    youtube_default_language: str = "en"
    require_manual_disclosure_before_public: bool = True

    # ---------- Notifications ----------
    notify_enabled: bool = True
    notifier: Literal["telegram", "none"] = "telegram"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_events: str = "run_complete,need_validation,video_uploaded,low_credits,run_failed"

    # ---------- Credit / Budget ----------
    monthly_budget_usd: float = 20.0
    low_credit_threshold_pct: float = 80.0

    # ---------- Storage ----------
    database_url: str = "sqlite:///data/career_engine.db"
    output_dir: str = "output/runs"

    # ---------- Safeguards ----------
    require_disclosure: bool = True
    require_grounding: bool = True

    # ---------- Ops ----------
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    schedule_cron: str = "0 9 * * MON"

    # ------------------------------------------------------------------ parsing
    @property
    def enabled_sources_list(self) -> list[str]:
        items = [s.strip().lower() for s in self.enabled_sources.split(",") if s.strip()]
        bad = [s for s in items if s not in VALID_SOURCES]
        if bad:
            raise ConfigError(
                f"Unknown data source(s) in ENABLED_SOURCES: {bad}. "
                f"Valid: {sorted(VALID_SOURCES)}"
            )
        return items

    @property
    def notify_events_list(self) -> list[str]:
        items = [e.strip() for e in self.notify_events.split(",") if e.strip()]
        bad = [e for e in items if e not in VALID_EVENTS]
        if bad:
            raise ConfigError(f"Unknown NOTIFY_EVENTS: {bad}. Valid: {sorted(VALID_EVENTS)}")
        return items

    @property
    def thumbnail_wh(self) -> tuple[int, int]:
        w, _, h = self.thumbnail_size.partition("x")
        return int(w), int(h)

    @property
    def resolution_wh(self) -> tuple[int, int]:
        w, _, h = self.video_resolution.partition("x")
        return int(w), int(h)

    # ------------------------------------------------------------- validation
    @model_validator(mode="after")
    def _validate_cross_fields(self) -> Settings:
        sources = self.enabled_sources_list  # also validates names
        if "adzuna" in sources and not (self.adzuna_app_id and self.adzuna_app_key):
            raise ValueError("adzuna is enabled but ADZUNA_APP_ID/ADZUNA_APP_KEY are not set")

        if self.fallback_provider != "none":
            key = (
                self.openai_api_key
                if self.fallback_provider == "openai"
                else self.anthropic_api_key
            )
            if not key:
                raise ValueError(
                    f"FALLBACK_PROVIDER={self.fallback_provider} requires its API key to be set"
                )

        if self.tts_provider == "elevenlabs" and not self.elevenlabs_api_key:
            raise ValueError("TTS_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY")

        if self.image_provider == "stability" and not self.stability_api_key:
            raise ValueError("IMAGE_PROVIDER=stability requires STABILITY_API_KEY")

        if self.render_backend == "avatar":
            if self.avatar_provider == "none":
                raise ValueError("RENDER_BACKEND=avatar requires AVATAR_PROVIDER != none")
            if self.avatar_provider == "heygen" and not self.heygen_api_key:
                raise ValueError("AVATAR_PROVIDER=heygen requires HEYGEN_API_KEY")

        if (
            self.publish_mode == "auto"
            and self.youtube_privacy_status == "public"
            and self.require_manual_disclosure_before_public
        ):
            raise ValueError(
                "Refusing auto-public while REQUIRE_MANUAL_DISCLOSURE_BEFORE_PUBLIC=true "
                "(synthetic-content disclosure is non-negotiable)"
            )

        if self.notify_enabled and self.notifier == "telegram" and not (
            self.telegram_bot_token and self.telegram_chat_id
        ):
            raise ValueError(
                "NOTIFY_ENABLED=true with NOTIFIER=telegram requires "
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )

        _ = self.notify_events_list  # validate parse
        return self

    # ----------------------------------------------------------- secrets/hash
    @staticmethod
    def _is_secret(name: str) -> bool:
        if name.endswith("_file"):
            return False
        return name.endswith(_SECRET_SUFFIXES)

    def redacted_dict(self) -> dict[str, object]:
        """Config dump with every secret replaced by ``***`` (or empty if unset)."""
        out: dict[str, object] = {}
        for name, value in self.model_dump().items():
            if self._is_secret(name):
                out[name] = "***" if value else ""
            else:
                out[name] = value
        return out

    def credential_status(self) -> dict[str, str]:
        """For ``career config check``: each secret as ``set ✓`` / ``missing ✗`` (never the value)."""
        return {
            name: ("set ✓" if value else "missing ✗")
            for name, value in self.model_dump().items()
            if self._is_secret(name)
        }

    @property
    def config_hash(self) -> str:
        blob = json.dumps(self.redacted_dict(), sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build (once) and return the shared :class:`Settings`. Fails fast on bad config."""
    try:
        return Settings()
    except ConfigError:
        raise
    except Exception as exc:  # pydantic ValidationError, etc.
        raise ConfigError(f"Invalid configuration: {exc}") from exc


def reset_settings_cache() -> None:
    """Clear the cached settings (used by tests after mutating the environment)."""
    get_settings.cache_clear()
