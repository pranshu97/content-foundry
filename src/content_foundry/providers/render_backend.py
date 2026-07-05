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
    from ..production.subscribe import SubscribeSpec
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
        citations_path: str | None = None,
        speed: float = 1.0,
        transition: str = "none",
        transition_sec: float = 0.5,
        color_warmth: float = 0.0,
        subscribe: SubscribeSpec | None = None,
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
        citations_path: str | None = None,
        speed: float = 1.0,
        transition: str = "none",
        transition_sec: float = 0.5,
        color_warmth: float = 0.0,
        subscribe: SubscribeSpec | None = None,
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
        crossfade = transition not in ("", "none") and len(segments) > 1
        pad = float(transition_sec) if crossfade else 0.0
        inputs = []
        for seg in segments:
            dur = max(seg.duration, 0.1) + pad
            if seg.visual_path.lower().endswith(video_exts):
                # B-roll clip: loop it to fill the scene's duration (the image-only `loop` option
                # is invalid for a video input and makes ffmpeg abort with "Option loop not found").
                stream = ffmpeg.input(seg.visual_path, stream_loop=-1, t=dur)
            else:
                # Still image (Pillow card / AI image): loop the single frame for the scene duration.
                stream = ffmpeg.input(seg.visual_path, loop=1, t=dur)
            stream = stream.filter("scale", width, height).filter("setsar", "1").filter("fps", fps)
            inputs.append(stream)
        if crossfade and inputs:  # pragma: no cover - requires ffmpeg on PATH
            # Blend consecutive scenes. Each clip is padded by the transition length and the xfade
            # offset sits on the narration boundary, so scenes stay in sync with the voiceover.
            video = _xfade_chain(
                inputs, [max(s.duration, 0.1) for s in segments], transition, pad
            )
        else:
            video = (
                ffmpeg.concat(*inputs, v=1, n=len(inputs)) if inputs else ffmpeg.input(audio_path)
            )
        if color_warmth and float(color_warmth) > 1e-3:  # pragma: no cover - requires ffmpeg
            w = min(max(float(color_warmth), 0.0), 1.0)
            # Push mids/highlights toward red and pull blue back for a warm, amber cast.
            video = video.filter(
                "colorbalance", rm=0.15 * w, bm=-0.12 * w, rh=0.10 * w, bh=-0.10 * w
            )
        if burn_captions and captions_path:
            video = video.filter("subtitles", captions_path)
        if citations_path:  # pragma: no cover - requires ffmpeg on PATH
            # Source citations pinned to the very top. Each cue also carries an inline {\an8}
            # override (see captions.build_scene_srt) which libass honours reliably; force_style is a
            # belt-and-suspenders default plus the compact font and opaque box.
            video = video.filter(
                "subtitles", citations_path,
                force_style="Alignment=8,MarginV=1,FontSize=12,BorderStyle=3,"
                "PrimaryColour=&H00FFFFFF&,BackColour=&HB0000000&",
            )
        if overlay is not None:  # pragma: no cover - requires ffmpeg on PATH
            avatar = ffmpeg.input(overlay.image_path).filter(
                "scale", -1, overlay.scaled_height(int(height))
            )
            x, y = overlay.ffmpeg_xy()
            video = ffmpeg.overlay(video, avatar, x=x, y=y)
        if subscribe is not None:  # pragma: no cover - requires ffmpeg on PATH
            # A badge that fades in at the midpoint, holds, then fades out — a gentle nudge.
            badge = (
                ffmpeg.input(subscribe.image_path, loop=1, t=subscribe.end + 1.0, framerate=fps)
                .filter("format", "rgba")
                .filter(
                    "fade", type="in", start_time=subscribe.start, duration=subscribe.fade, alpha=1
                )
                .filter(
                    "fade", type="out",
                    start_time=max(subscribe.start, subscribe.end - subscribe.fade),
                    duration=subscribe.fade, alpha=1,
                )
            )
            bx, by = subscribe.ffmpeg_xy()
            video = ffmpeg.overlay(
                video, badge, x=bx, y=by,
                enable=f"between(t,{subscribe.start},{subscribe.end})",
            )
        audio = ffmpeg.input(audio_path)
        if speed and abs(speed - 1.0) > 1e-3:  # pragma: no cover - requires ffmpeg on PATH
            # Play the whole thing faster/slower: compress the video PTS and time-stretch the audio
            # (pitch preserved). Burned captions/citations live in the video stream, so they stay in
            # sync automatically.
            video = video.filter("setpts", f"{1.0 / speed}*PTS")
            audio = _apply_atempo(audio, speed)
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


def _xfade_chain(streams, durations, transition, dur):  # pragma: no cover - requires ffmpeg
    """Chain ffmpeg xfade across scene ``streams``. Each clip is padded by ``dur`` and the k-th
    transition offset is the cumulative narration length, keeping visuals aligned to the voiceover."""
    import ffmpeg

    trans = transition if transition not in ("", "none") else "fade"
    acc = streams[0]
    offset = 0.0
    for i in range(1, len(streams)):
        offset += max(durations[i - 1], 0.1)
        acc = ffmpeg.filter(
            [acc, streams[i]], "xfade", transition=trans, duration=dur, offset=round(offset, 3)
        )
    return acc


def _apply_atempo(audio, speed: float):  # pragma: no cover - requires ffmpeg on PATH
    """Time-stretch audio by ``speed`` with pitch preserved. atempo handles 0.5-2.0 per pass, so
    chain it for larger factors."""
    remaining = speed
    while remaining > 2.0:
        audio = audio.filter("atempo", 2.0)
        remaining /= 2.0
    while remaining < 0.5:
        audio = audio.filter("atempo", 0.5)
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-3:
        audio = audio.filter("atempo", round(remaining, 4))
    return audio


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
