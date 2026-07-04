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
from datetime import UTC, datetime
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
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- LLM Providers ----------
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    primary_provider: Literal["anthropic", "openai", "local"] = "anthropic"
    fallback_provider: Literal["anthropic", "openai", "local", "none"] = "openai"
    generator_model: str = "claude-sonnet-4-20250514"
    judge_model: str = "claude-sonnet-4-20250514"
    llm_temperature: float = 0.7
    judge_temperature: float = 0.0
    llm_max_tokens: int = 4096
    # Tiered model routing (future plan 2): heavy for hard creative work, light for
    # mechanical / high-volume calls. Empty => fall back to generator/judge models.
    llm_tiering_enabled: bool = True
    model_heavy: str = ""
    model_light: str = ""
    # Local / self-hosted LLM (cost saver): any OpenAI-compatible server (Ollama, LM Studio,
    # vLLM, llama.cpp, LocalAI). Active when PRIMARY/FALLBACK_PROVIDER=local; LOCAL_LLM_MODEL is
    # used for every call (the cloud GENERATOR/JUDGE/heavy/light model names are ignored).
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "llama3.1"
    local_llm_api_key: str = "local"

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
    # Brainstormer (Agent 0): an LLM proposes a fresh, specific video idea each run to avoid topic
    # collapse; falls back to a deterministic content angle. Disable to reuse the raw topic/niche.
    brainstorm_enabled: bool = True
    brainstorm_idea_count: int = Field(5, ge=1, le=10)
    script_target_words: int = 900
    min_facts: int = 3
    # Completeness gate (Ch. 9.3a): reject stub scripts the quality rubric would otherwise pass. A
    # grounded single-scene draft scores well on every dimension but is far too short for a video.
    min_scenes: int = Field(3, ge=1)
    min_script_word_ratio: float = Field(0.5, ge=0, le=1)
    # A genuinely excellent draft (weighted_total >= gate_relief_score) earns `gate_relief_ratio`
    # slack on the insight & length floors ONLY — never on grounding, compliance, or fatigue.
    # Set gate_relief_score > 10 to disable.
    gate_relief_score: float = Field(9.0, ge=0, le=11)
    gate_relief_ratio: float = Field(0.20, ge=0, le=0.5)
    # 0 = disabled (default). When > 0, abort the revision loop once a script still scores below
    # this weighted total on attempt >= 2 — it can't realistically reach PASS, so stop paying.
    fail_fast_score: float = Field(0.0, ge=0, le=10)
    # Human-in-the-loop: when true, a PASSed script pauses before production (voiceover onward) so you
    # can review script.json, then `content-foundry resume` to continue. Default off (fully automatic).
    require_script_approval: bool = False

    # ---------- Voiceover (TTS) ----------
    tts_provider: Literal["elevenlabs", "openai", "edge", "piper"] = "elevenlabs"
    elevenlabs_api_key: str = ""
    tts_voice_id: str = "Rachel"
    tts_model: str = "eleven_multilingual_v2"
    tts_format: str = "mp3_44100_128"
    # Free voices: edge = Microsoft neural (online, no key); piper = fully offline (needs a .onnx model).
    piper_model_path: str = ""
    piper_executable: str = "piper"

    # ---------- Visuals ----------
    image_provider: Literal["openai", "stability", "none"] = "openai"
    stability_api_key: str = ""
    pexels_api_key: str = ""
    # How many candidate clips to pull per B-roll query so each scene can get a distinct clip
    # (and no clip repeats more than twice across the video). Pexels allows up to 80 per page.
    broll_pool_size: int = Field(15, ge=1, le=80)
    visual_style: str = "clean infographic, high-contrast, bold text"
    scenes_per_video: int = 10
    thumbnail_size: str = "1280x720"

    # ---------- Render ----------
    render_backend: Literal["ffmpeg", "moviepy", "avatar"] = "ffmpeg"
    ffmpeg_path: str = ""  # optional explicit path to ffmpeg(.exe); auto-discovered when blank
    avatar_provider: Literal["none", "heygen", "did"] = "none"
    heygen_api_key: str = ""
    video_resolution: str = "1920x1080"
    video_fps: int = 30
    captions_enabled: bool = True
    caption_aligner: Literal["tts", "whisper"] = "tts"
    render_fallback: bool = True
    # Personal avatar overlay (future plan 1): composited at a fixed corner of every frame.
    # The image is operator-supplied; rendering skips gracefully when the file is absent.
    avatar_overlay_enabled: bool = False
    avatar_image_path: str = "assets/avatar.png"
    avatar_position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "bottom-right"
    avatar_scale: float = Field(0.18, gt=0, le=1)
    avatar_margin: int = Field(24, ge=0)

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
    # Hard cap: abort a run once estimated month-to-date spend reaches the budget (cost safety).
    enforce_budget_cap: bool = True

    # ---------- Storage ----------
    database_url: str = "sqlite:///data/content_foundry.db"
    output_dir: str = "output/runs"

    # ---------- Safeguards ----------
    require_disclosure: bool = True
    require_grounding: bool = True

    # ---------- Content strategy (future plans 3-5) ----------
    time_box_enabled: bool = True
    content_year: int = Field(0, ge=0)  # 0 => current UTC year
    seo_optimize_enabled: bool = True
    seo_max_tags: int = Field(15, ge=0)
    seo_title_max_chars: int = Field(70, ge=10)
    seo_add_chapters: bool = True
    channel_keywords: str = ""  # comma list of evergreen channel tags (optional)

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

    @property
    def heavy_model(self) -> str:
        """Model for hard, creative, low-volume work (script generation)."""
        return self.model_heavy or self.generator_model

    @property
    def light_model(self) -> str:
        """Model for mechanical / high-volume work (JSON repair, judge scoring)."""
        return self.model_light or self.judge_model or self.generator_model

    @property
    def effective_content_year(self) -> int:
        """Year used for time-boxing titles; ``content_year`` or the current UTC year."""
        return self.content_year or datetime.now(UTC).year

    @property
    def channel_keywords_list(self) -> list[str]:
        return [k.strip() for k in self.channel_keywords.split(",") if k.strip()]

    # ------------------------------------------------------------- validation
    @model_validator(mode="after")
    def _validate_cross_fields(self) -> Settings:
        sources = self.enabled_sources_list  # also validates names
        if "adzuna" in sources and not (self.adzuna_app_id and self.adzuna_app_key):
            raise ValueError("adzuna is enabled but ADZUNA_APP_ID/ADZUNA_APP_KEY are not set")

        if "local" in (self.primary_provider, self.fallback_provider) and not self.local_llm_base_url:
            raise ValueError(
                "PRIMARY_PROVIDER/FALLBACK_PROVIDER=local requires LOCAL_LLM_BASE_URL"
            )

        if self.fallback_provider not in ("none", "local"):
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

        if self.tts_provider == "piper" and not self.piper_model_path:
            raise ValueError("TTS_PROVIDER=piper requires PIPER_MODEL_PATH (path to a .onnx voice)")

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
        """For ``content-foundry config check``: each secret as ``set ✓`` / ``missing ✗`` (never the value)."""
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
        # Resolve the dotenv path at call time so ``ENV_FILE`` can repoint it (and tests stay hermetic).
        return Settings(_env_file=os.environ.get("ENV_FILE", ".env"))
    except ConfigError:
        raise
    except Exception as exc:  # pydantic ValidationError, etc.
        raise ConfigError(f"Invalid configuration: {exc}") from exc


def reset_settings_cache() -> None:
    """Clear the cached settings (used by tests after mutating the environment)."""
    get_settings.cache_clear()
