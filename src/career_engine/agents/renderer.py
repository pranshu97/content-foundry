"""Agent 6 — Video Renderer. Deterministic assembly of audio + visuals + captions (Ch. 12)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, VideoAsset, VisualPackage, VoiceoverAsset
from ..production.timeline import build_timeline

_VIDEO_REL = "assets/video.mp4"


class Renderer:
    def __init__(self, settings, render_backend):
        self._settings = settings
        self._backend = render_backend
        self._log = get_logger(component="renderer")

    def run(
        self, run_id: str, voiceover: VoiceoverAsset, visuals: VisualPackage, *, run_root: Path
    ) -> VideoAsset:
        segments = build_timeline(voiceover, visuals)
        resolved = [
            replace(seg, visual_path=str(run_root / seg.visual_path) if seg.visual_path else "")
            for seg in segments
        ]
        audio_real = str(run_root / voiceover.audio_path)
        captions_real = (
            str(run_root / visuals.captions_path) if self._settings.captions_enabled else None
        )
        out_real = run_root / _VIDEO_REL
        out_real.parent.mkdir(parents=True, exist_ok=True)

        path = self._backend.render(
            segments=resolved,
            audio_path=audio_real,
            captions_path=captions_real,
            output_path=str(out_real),
            resolution=self._settings.video_resolution,
            fps=self._settings.video_fps,
            burn_captions=self._settings.captions_enabled,
        )

        size = Path(path).stat().st_size if Path(path).exists() else 0
        return VideoAsset(
            run_id=run_id,
            video_path=_VIDEO_REL,
            duration_sec=voiceover.duration_sec,
            resolution=self._settings.video_resolution,
            fps=self._settings.video_fps,
            backend=getattr(self._backend, "name", self._settings.render_backend),
            has_captions=self._settings.captions_enabled,
            file_size_bytes=size,
            provenance=Provenance(
                produced_by="renderer", model=None, config_hash=self._settings.config_hash
            ),
        )
