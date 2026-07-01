"""Render-backend protocol + ffmpeg/moviepy/avatar adapters (Ch. 12.4). Imports lazy."""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..errors import RenderError

if TYPE_CHECKING:
    from ..production.overlay import OverlaySpec
    from ..production.timeline import RenderSegment


def resolve_ffmpeg(configured: str = "") -> str | None:
    """Find the ffmpeg executable: explicit config path, then PATH, then common install dirs.

    Robust to editors/shells launched before ffmpeg was added to PATH (a frequent Windows gotcha),
    so rendering works without needing to restart the whole editor.
    """
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    on_path = shutil.which("ffmpeg")
    if on_path:
        candidates.append(on_path)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(str(Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"))
    candidates += [
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    if local:  # last resort: the versioned WinGet package folder
        matches = sorted(Path(local, "Microsoft", "WinGet", "Packages").glob("*FFmpeg*/**/ffmpeg.exe"))
        if matches:
            return str(matches[0])
    return None


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
        overlay: OverlaySpec | None = None,
    ) -> str:
        """Assemble the final mp4 and return its path."""
        ...


class FfmpegBackend:
    """Default faceless slideshow + B-roll + burned-in captions via an ffmpeg filtergraph."""

    name = "ffmpeg"

    def __init__(self, ffmpeg_path: str = "") -> None:
        self._ffmpeg = ffmpeg_path

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
        overlay: OverlaySpec | None = None,
    ) -> str:
        exe = resolve_ffmpeg(self._ffmpeg)
        if exe is None:
            raise RenderError(
                "ffmpeg not found. Install it, then FULLY restart your editor so the new PATH is "
                "picked up — or set FFMPEG_PATH in .env. Windows `winget install Gyan.FFmpeg`, "
                "macOS `brew install ffmpeg`, Debian/Ubuntu `sudo apt-get install -y ffmpeg`."
            )
        import ffmpeg  # lazy

        width, _, height = resolution.partition("x")
        video_exts = (".mp4", ".mov", ".webm", ".mkv", ".avi")
        inputs = []
        for seg in segments:
            dur = max(seg.duration, 0.1)
            if seg.visual_path.lower().endswith(video_exts):
                # B-roll clip: loop it to fill the scene's duration (the image-only `loop` option
                # is invalid for a video input and makes ffmpeg abort with "Option loop not found").
                stream = ffmpeg.input(seg.visual_path, stream_loop=-1, t=dur)
            else:
                # Still image (Pillow card / AI image): loop the single frame for the scene duration.
                stream = ffmpeg.input(seg.visual_path, loop=1, t=dur)
            stream = stream.filter("scale", width, height).filter("setsar", "1").filter("fps", fps)
            inputs.append(stream)
        video = ffmpeg.concat(*inputs, v=1, n=len(inputs)) if inputs else ffmpeg.input(audio_path)
        if burn_captions and captions_path:
            video = video.filter("subtitles", captions_path)
        if overlay is not None:  # pragma: no cover - requires ffmpeg on PATH
            avatar = ffmpeg.input(overlay.image_path).filter(
                "scale", -1, overlay.scaled_height(int(height))
            )
            x, y = overlay.ffmpeg_xy()
            video = ffmpeg.overlay(video, avatar, x=x, y=y)
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
                .run(cmd=exe, quiet=True)
            )
        except Exception as exc:  # ffmpeg.Error
            stderr = getattr(exc, "stderr", None)
            detail = stderr.decode("utf-8", "ignore").strip() if stderr else str(exc)
            raise RenderError(f"ffmpeg render failed:\n{detail[-1200:]}") from exc
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
