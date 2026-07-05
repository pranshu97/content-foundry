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
    from .tts import OpenAITTS

    return OpenAITTS(settings.openai_api_key, voice)


def build_image_provider(settings: Settings) -> ImageProvider | None:
    if settings.image_provider == "none":
        return None
    if settings.image_provider == "openai":
        from .image import OpenAIImage

        return OpenAIImage(settings.openai_api_key)
    from .image import StabilityImage

    return StabilityImage(settings.stability_api_key)


def build_broll_client(settings: Settings) -> BrollClient:
    clients = []
    if settings.pexels_api_key:
        from .broll import PexelsBrollClient

        clients.append(PexelsBrollClient(settings.pexels_api_key, settings.broll_pool_size))
    if settings.pixabay_api_key:
        from .broll import PixabayBrollClient

        clients.append(PixabayBrollClient(settings.pixabay_api_key, settings.broll_pool_size))
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

        return FfmpegBackend(settings.ffmpeg_path)
    if settings.render_backend == "moviepy":
        from .render_backend import MoviePyBackend

        return MoviePyBackend()
    from .render_backend import AvatarBackend, FfmpegBackend

    fallback = FfmpegBackend(settings.ffmpeg_path) if settings.render_fallback else None
    return AvatarBackend(settings.avatar_provider, settings.heygen_api_key, fallback)


def build_sfx_client(settings: Settings):
    if not settings.sfx_enabled:
        from .sfx import NullSfxClient

        return NullSfxClient()
    from .sfx import SfxLibrary

    return SfxLibrary(settings.sfx_dir, freesound_api_key=settings.freesound_api_key)


def build_publisher(settings: Settings, *, dry_run: bool = False) -> Publisher:
    if dry_run:
        from .youtube import DryRunPublisher

        return DryRunPublisher()
    from .youtube import YouTubePublisher

    return YouTubePublisher(settings.youtube_client_secrets_file, settings.youtube_token_file)


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
]
