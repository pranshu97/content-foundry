"""Provider factories — build vendor adapters from :class:`Settings`.

Concrete adapter *classes* are imported lazily inside the factories, and each adapter imports its
vendor SDK lazily inside its methods, so importing this package never requires any vendor SDK.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import ConfigError
from .base import LLMProvider, LLMResponse, extract_json
from .fallback import FallbackProvider

if TYPE_CHECKING:
    from ..config import Settings
    from .broll import BrollClient
    from .image import ImageProvider
    from .render_backend import RenderBackend
    from .tts import TTSProvider
    from .youtube import Publisher
    from .youtube_data import YouTubeDataClient


def _chain_llms(providers: list[LLMProvider], *, latch_all: bool = False) -> LLMProvider:
    """Fold a best-first list of providers into a right-nested FallbackProvider chain: ``providers[0]``
    is the primary and each later one takes over when the earlier fails. A single provider is returned
    as-is (no wrapper)."""
    chain = providers[-1]
    for p in reversed(providers[:-1]):
        chain = FallbackProvider(p, chain, latch_all=latch_all)
    return chain


def _make_single_llm(name: str, settings: Settings) -> LLMProvider:
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.generator_model)
    if name == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(api_key=settings.openai_api_key, model=settings.generator_model)
    if name == "local":
        from .local_provider import LocalLLMProvider

        return LocalLLMProvider(
            base_url=settings.local_llm_base_url,
            model=settings.local_llm_model,
            api_key=settings.local_llm_api_key,
        )
    if name == "google":
        from .google_provider import GoogleProvider

        models = settings.google_models_list or ["gemini-2.5-flash"]
        # Best-first Gemini chain: each model tried in turn, the next taking over on ANY error
        # (quota/404/network), before the outer FALLBACK_PROVIDER (e.g. local) is ever reached.
        return _chain_llms(
            [
                GoogleProvider(
                    api_key=settings.google_api_key, model=m,
                    top_p=settings.llm_top_p, thinking=settings.google_thinking,
                )
                for m in models
            ],
            latch_all=True,
        )
    raise ConfigError(f"Unknown LLM provider: {name}")


def build_llm_provider(settings: Settings) -> LLMProvider:
    primary = _make_single_llm(settings.primary_provider, settings)
    secondary: LLMProvider | None = None
    if settings.fallback_provider != "none":
        secondary = _make_single_llm(settings.fallback_provider, settings)
    return FallbackProvider(primary, secondary)


def build_tts_provider(settings: Settings, *, run_id: str | None = None) -> TTSProvider:
    from .tts import pick_voice

    voice = pick_voice(
        run_id,
        male=settings.tts_voice_male,
        female=settings.tts_voice_female,
        default=settings.tts_voice_id,
    )
    if settings.tts_provider == "elevenlabs":
        from .tts import ElevenLabsTTS

        return ElevenLabsTTS(
            settings.elevenlabs_api_key,
            voice,
            settings.tts_model,
            settings.tts_format,
        )
    if settings.tts_provider == "edge":
        from .tts import EdgeTTS

        return EdgeTTS(voice)
    if settings.tts_provider == "piper":
        from .tts import PiperTTS

        return PiperTTS(settings.piper_model_path, settings.piper_executable)
    if settings.tts_provider == "chatterbox":
        from .tts import ChatterboxTTS

        return ChatterboxTTS(
            settings.tts_reference_clip,
            device=settings.tts_clone_device,
            exaggeration=settings.tts_clone_exaggeration,
            cfg_weight=settings.tts_clone_cfg,
            silence_pad_ms=settings.tts_silence_pad_ms,
        )
    from .tts import OpenAITTS

    return OpenAITTS(settings.openai_api_key, voice)


def _make_single_image(name: str, settings: Settings) -> ImageProvider | None:
    if name == "none":
        return None
    if name == "openai":
        from .image import OpenAIImage

        return OpenAIImage(settings.openai_api_key)
    if name == "google":
        from .image import GoogleImage

        return GoogleImage(settings.google_api_key, settings.google_image_model)
    if name == "pollinations":
        from .image import PollinationsImage

        return PollinationsImage()
    from .image import StabilityImage

    return StabilityImage(settings.stability_api_key)


def build_image_provider(settings: Settings) -> ImageProvider | None:
    primary = _make_single_image(settings.image_provider, settings)
    if primary is None:
        return None
    secondary = _make_single_image(settings.image_fallback_provider, settings)
    if secondary is None:
        return primary
    from .image import FallbackImageProvider

    return FallbackImageProvider(primary, secondary)


def build_broll_client(settings: Settings) -> BrollClient:
    clients = []
    if settings.pexels_api_key:
        from .broll import PexelsBrollClient

        clients.append(PexelsBrollClient(settings.pexels_api_key, settings.broll_pool_size))
    if settings.pixabay_api_key:
        from .broll import PixabayBrollClient

        clients.append(PixabayBrollClient(settings.pixabay_api_key, settings.broll_pool_size))
    if settings.coverr_api_key:
        from .broll import CoverrBrollClient

        clients.append(CoverrBrollClient(settings.coverr_api_key, settings.broll_pool_size))
    if not clients:
        from .broll import NullBrollClient

        return NullBrollClient()
    if len(clients) == 1:
        return clients[0]
    from .broll import MultiBrollClient

    return MultiBrollClient(clients)


def build_render_backend(settings: Settings) -> RenderBackend:
    if settings.render_backend == "ffmpeg":
        from .render_backend import FfmpegBackend

        return FfmpegBackend(settings.ffmpeg_path, settings.video_encoder)
    if settings.render_backend == "moviepy":
        from .render_backend import MoviePyBackend

        return MoviePyBackend()
    from .render_backend import AvatarBackend, FfmpegBackend

    fallback = (
        FfmpegBackend(settings.ffmpeg_path, settings.video_encoder)
        if settings.render_fallback
        else None
    )
    return AvatarBackend(settings.avatar_provider, settings.heygen_api_key, fallback)


def _bundled_sfx_dir():
    """The SFX clip library packaged inside content_foundry (ships via package-data), used when the
    configured sfx_dir is absent (e.g. a pip install has no repo-root ``data/sounds``)."""
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / "data" / "sounds"


def build_sfx_client(settings: Settings):
    if not settings.sfx_enabled:
        from .sfx import NullSfxClient

        return NullSfxClient()
    from pathlib import Path

    from .sfx import SfxLibrary

    # Prefer the configured dir; fall back to the sound library BUNDLED in the installed package.
    sounds_dir = settings.sfx_dir if Path(settings.sfx_dir).is_dir() else str(_bundled_sfx_dir())
    return SfxLibrary(sounds_dir, freesound_api_key=settings.freesound_api_key)


def build_publisher(settings: Settings, *, dry_run: bool = False) -> Publisher:
    if dry_run:
        from .youtube import DryRunPublisher

        return DryRunPublisher()
    from .youtube import YouTubePublisher

    return YouTubePublisher(
        settings.youtube_client_secrets_file, settings.youtube_token_file,
        comment_enabled=settings.publish_top_comment or settings.recommend_comment_enabled,
    )


def build_youtube_data_client(settings: Settings) -> YouTubeDataClient:
    """Read-only Data-API client for proven-idea mining; a disabled null client when no key is set."""
    key = (settings.youtube_api_key or "").strip()
    if not key:
        from .youtube_data import NullYouTubeDataClient

        return NullYouTubeDataClient()
    from .youtube_data import ApiYouTubeDataClient

    return ApiYouTubeDataClient(key)


__all__ = [
    "LLMProvider",
    "LLMResponse",
    "extract_json",
    "FallbackProvider",
    "build_llm_provider",
    "build_tts_provider",
    "build_image_provider",
    "build_broll_client",
    "build_sfx_client",
    "build_render_backend",
    "build_publisher",
    "build_youtube_data_client",
]
