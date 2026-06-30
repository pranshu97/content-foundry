"""Render-backend protocol + ffmpeg/moviepy/avatar adapters (Ch. 12.4). Imports lazy."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..errors import RenderError

if TYPE_CHECKING:
    from ..production.timeline import RenderSegment


@runtime_checkable
class RenderBackend(Protocol):
    name: str

    def render(
        self,
        *,
        segments: Sequence[RenderSegment],
        audio_path: str,
        captions_path: str | None,
        output_path: str,
        resolution: str,
        fps: int,
        burn_captions: bool = True,
    ) -> str:
        """Assemble the final mp4 and return its path."""
        ...


class FfmpegBackend:
    """Default faceless slideshow + B-roll + burned-in captions via an ffmpeg filtergraph."""

    name = "ffmpeg"

    def render(
        self,
        *,
        segments: Sequence[RenderSegment],
        audio_path: str,
        captions_path: str | None,
        output_path: str,
        resolution: str,
        fps: int,
        burn_captions: bool = True,
    ) -> str:
        if shutil.which("ffmpeg") is None:
            raise RenderError(
                "ffmpeg not found on PATH. Install it (see Ch. 23): "
                "Windows `winget install Gyan.FFmpeg`, macOS `brew install ffmpeg`, "
                "Debian/Ubuntu `sudo apt-get install -y ffmpeg`."
            )
        import ffmpeg  # lazy

        width, _, height = resolution.partition("x")
        inputs = []
        for seg in segments:
            stream = ffmpeg.input(seg.visual_path, loop=1, t=max(seg.duration, 0.1))
            stream = stream.filter("scale", width, height).filter("setsar", "1")
            inputs.append(stream)
        video = ffmpeg.concat(*inputs, v=1, n=len(inputs)) if inputs else ffmpeg.input(audio_path)
        if burn_captions and captions_path:
            video = video.filter("subtitles", captions_path)
        audio = ffmpeg.input(audio_path)
        try:
            (
                ffmpeg.output(
                    video,
                    audio,
                    output_path,
                    vcodec="libx264",
                    acodec="aac",
                    pix_fmt="yuv420p",
                    r=fps,
                    shortest=None,
                )
                .overwrite_output()
                .run(quiet=True)
            )
        except Exception as exc:  # ffmpeg.Error
            raise RenderError(f"ffmpeg render failed: {exc}") from exc
        return output_path


class MoviePyBackend:  # pragma: no cover - optional heavy backend
    name = "moviepy"

    def render(self, **kwargs: object) -> str:
        raise RenderError("MoviePyBackend is optional and not enabled in this build.")


class AvatarBackend:  # pragma: no cover - optional talking-head backend
    name = "avatar"

    def __init__(self, provider: str, api_key: str, fallback: RenderBackend | None = None) -> None:
        self._provider = provider
        self._api_key = api_key
        self._fallback = fallback

    def render(self, **kwargs: object) -> str:
        raise RenderError("AvatarBackend requires a configured HeyGen/D-ID provider.")
