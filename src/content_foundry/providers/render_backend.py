"""Render-backend protocol + ffmpeg/moviepy/avatar adapters (Ch. 12.4). Imports lazy."""

from __future__ import annotations

import os
import re
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


def _probe_seconds(path: str) -> float:
    """Best-effort clip duration in seconds via ffprobe; 0.0 when it can't be determined (the caller
    then freeze-pads to fill the beat instead of guessing — it still never loops the clip)."""
    try:
        import ffmpeg

        return float(ffmpeg.probe(path)["format"]["duration"])
    except Exception:
        return 0.0


def _scale_cover(stream, width, height):  # pragma: no cover - requires ffmpeg on PATH
    """Scale to COVER the frame (aspect preserved) then centre-crop to exactly WxH — so 16:9 stock
    fills a 9:16 Short (and any off-ratio clip fills a 16:9 frame) WITHOUT stretching or distortion.
    Correct for both formats: for a same-ratio source the crop is a no-op."""
    return (
        stream.filter("scale", width, height, force_original_aspect_ratio="increase")
        .filter("crop", width, height)
    )


_ENCODER_CACHE: dict[str, set[str]] = {}
_WORKING_ENCODER_CACHE: dict[str, str | None] = {}
# Preference order for automatic GPU encoder selection: NVIDIA NVENC, Intel Quick Sync, AMD AMF.
_HW_ENCODER_PREFERENCE = ("h264_nvenc", "h264_qsv", "h264_amf")


def _available_encoders(exe: str) -> set[str]:
    """The GPU/CPU H.264/HEVC encoder names this ffmpeg build exposes (cached per binary)."""
    if exe not in _ENCODER_CACHE:
        try:
            import subprocess

            out = subprocess.run(
                [exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15
            ).stdout
        except Exception:
            out = ""
        pat = r"\b(h264_nvenc|hevc_nvenc|h264_qsv|hevc_qsv|h264_amf|hevc_amf|libx264)\b"
        _ENCODER_CACHE[exe] = set(re.findall(pat, out))
    return _ENCODER_CACHE[exe]


def _probe_encoder(exe: str, encoder: str) -> bool:
    """Return True only when this ffmpeg build can ACTUALLY run the encoder end-to-end — not just
    that it is compiled in. Runs a silent sub-second test encode (discards output). Uses 256x144
    so hardware encoders that enforce a minimum resolution (e.g. AMF) also pass the probe."""
    import subprocess
    import tempfile

    opts = list(_encoder_opts(encoder).items())
    extra: list[str] = [item for k, v in opts for item in [f"-{k}", str(v)]]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "probe.mp4")
        r = subprocess.run(
            [exe, "-y", "-f", "lavfi",
             "-i", "color=black:size=256x144:rate=25",
             "-t", "0.2", "-c:v", encoder] + extra + [out],
            capture_output=True, timeout=15,
        )
        return r.returncode == 0 and os.path.getsize(out) > 0


def _select_encoder(exe: str, configured: str) -> str:
    """Resolve the video encoder: an explicit non-auto setting wins; 'auto'/'' probes each GPU
    encoder in preference order and picks the first one that actually works, else CPU libx264.
    Results are cached per binary so the probe runs at most once per session."""
    choice = (configured or "auto").strip().lower()
    if choice not in ("", "auto"):
        return choice  # user forced a specific encoder — trust them
    if exe not in _WORKING_ENCODER_CACHE:
        avail = _available_encoders(exe)
        picked = None
        for enc in _HW_ENCODER_PREFERENCE:
            if enc in avail and _probe_encoder(exe, enc):
                picked = enc
                break
        _WORKING_ENCODER_CACHE[exe] = picked
    return _WORKING_ENCODER_CACHE[exe] or "libx264"


def _encoder_opts(encoder: str) -> dict:
    """Modest quality/speed options per encoder family so files stay small and the encode is fast."""
    if encoder.endswith("_nvenc"):
        return {"preset": "p5", "rc": "vbr", "cq": 23}
    if encoder.endswith("_qsv"):
        return {"global_quality": 23, "preset": "faster"}
    if encoder.endswith("_amf"):
        return {"quality": "balanced", "rc": "vbr_latency"}
    return {}  # libx264: ffmpeg defaults (crf 23, preset medium)


def _run_ffmpeg(stream, exe: str, output_path: str) -> None:  # pragma: no cover - requires ffmpeg
    """Run an ffmpeg-python graph, passing a large ``-filter_complex`` via a SCRIPT FILE instead of
    inline. A rich B-roll timeline builds a filtergraph tens of thousands of characters long, and
    Windows caps a whole command line at ~32K (``WinError 206: filename or extension too long``);
    ``-filter_complex_script`` keeps the graph off the CLI so the render scales to any scene/shot
    count. The remaining args (inputs, output opts) stay well within the limit."""
    import subprocess

    import ffmpeg

    args = list(stream.get_args())
    script_path = None
    if "-filter_complex" in args:
        idx = args.index("-filter_complex")
        script_path = f"{output_path}.filtergraph.txt"
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(args[idx + 1])
        args[idx] = "-filter_complex_script"
        args[idx + 1] = script_path
    try:
        proc = subprocess.run([exe, *args], capture_output=True)
    finally:
        if script_path and os.path.exists(script_path):
            os.remove(script_path)
    if proc.returncode != 0:
        raise ffmpeg.Error("ffmpeg", proc.stdout, proc.stderr)


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

    def __init__(self, ffmpeg_path: str = "", video_encoder: str = "auto") -> None:
        self._ffmpeg = ffmpeg_path
        self._encoder = video_encoder

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
            # A scene may be a sequence of beat clips (finer B-roll); build them, then treat the
            # whole scene as one stream so captions, citations, sfx and transitions stay scene-level.
            beats = list(seg.clips) or [(seg.visual_path, max(seg.duration, 0.1))]
            substreams = []
            last = len(beats) - 1
            for j, (path, clip_dur) in enumerate(beats):
                d = max(clip_dur, 0.1) + (pad if j == last else 0.0)  # pad only the scene's tail
                if path.lower().endswith(video_exts):
                    # Fit the clip to its beat WITHOUT looping — a short clip looping mid-scene is the
                    # "same b-roll 3x" artifact. A too-short clip is slowed to fill the beat (smooth
                    # slow-mo); a longer clip is simply trimmed. tpad freezes the final frame only as a
                    # last-resort safety when the clip length can't be probed, so we never loop.
                    v = ffmpeg.input(path).video
                    clen = _probe_seconds(path)
                    if clen and clen + 0.05 < d:
                        v = v.filter("setpts", f"{d / clen:.6f}*PTS")
                    s = (
                        _scale_cover(v, width, height).filter("setsar", "1").filter("fps", fps)
                        .filter("tpad", stop_mode="clone", stop_duration=d)
                        .trim(duration=d)
                        .filter("setpts", "PTS-STARTPTS")
                    )
                else:
                    s = (
                        _scale_cover(ffmpeg.input(path, loop=1, t=d), width, height)
                        .filter("setsar", "1").filter("fps", fps)
                    )
                substreams.append(s)
            seg_stream = (
                ffmpeg.concat(*substreams, v=1, n=len(substreams))
                if len(substreams) > 1
                else substreams[0]
            )
            inputs.append(seg_stream)
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
        encoder = _select_encoder(exe, self._encoder)
        try:
            self._encode(video, audio, output_path, encoder, fps, exe)
            return output_path
        except Exception as exc:  # ffmpeg.Error
            error = exc
        # A GPU encoder can fail (driver/session limits, an unsupported pixel format) — fall back to
        # CPU libx264 so a render never dies just because hardware encoding was unavailable.
        if encoder != "libx264":
            try:
                self._encode(video, audio, output_path, "libx264", fps, exe)
                return output_path
            except Exception as exc:
                error = exc
        stderr = getattr(error, "stderr", None)
        detail = stderr.decode("utf-8", "ignore").strip() if stderr else str(error)
        raise RenderError(f"ffmpeg render failed:\n{detail[-1200:]}") from error

    def _encode(self, video, audio, output_path, encoder, fps, exe):  # pragma: no cover - ffmpeg
        import ffmpeg

        stream = (
            ffmpeg.output(
                video,
                audio,
                output_path,
                vcodec=encoder,
                acodec="aac",
                pix_fmt="yuv420p",
                r=fps,
                shortest=None,
                **_encoder_opts(encoder),
            )
            .overwrite_output()
        )
        _run_ffmpeg(stream, exe, output_path)


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
