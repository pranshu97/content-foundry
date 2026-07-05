"""Agent 6 — Video Renderer. Deterministic assembly of audio + visuals + captions (Ch. 12)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, VideoAsset, VisualPackage, VoiceoverAsset
from ..production.captions import source_only, write_scene_srt
from ..production.overlay import build_overlay_spec
from ..production.sound_design import mix_sfx
from ..production.subscribe import build_subscribe_spec
from ..production.timeline import build_timeline

_VIDEO_REL = "assets/video.mp4"


class Renderer:
    def __init__(self, settings, render_backend, sfx_client=None):
        self._settings = settings
        self._backend = render_backend
        self._sfx = sfx_client
        self._log = get_logger(component="renderer")

    def run(
        self, run_id: str, voiceover: VoiceoverAsset, visuals: VisualPackage, *, run_root: Path
    ) -> VideoAsset:
        segments = build_timeline(voiceover, visuals)
        resolved = [
            replace(
                seg,
                visual_path=str(run_root / seg.visual_path) if seg.visual_path else "",
                clips=tuple((str(run_root / p), d) for p, d in seg.clips),
            )
            for seg in segments
        ]
        audio_real = str(run_root / voiceover.audio_path)
        if self._sfx is not None and getattr(self._sfx, "enabled", False):
            cues = [(seg.start, seg.sfx) for seg in segments if seg.sfx]
            if cues:
                mixed = run_root / "assets/narration_mixed.mp3"
                if mix_sfx(
                    audio_real, cues, self._sfx, mixed, gain_db=self._settings.sfx_volume_db
                ):
                    audio_real = str(mixed)
        captions_real = (
            str(run_root / visuals.captions_path) if self._settings.captions_enabled else None
        )
        citations_rel = "assets/citations.srt"
        has_citations = write_scene_srt(
            run_root / citations_rel,
            [(s.start, s.end, source_only(s.on_screen_text or "")) for s in segments],
        )
        citations_real = str(run_root / citations_rel) if has_citations else None
        out_real = run_root / _VIDEO_REL
        out_real.parent.mkdir(parents=True, exist_ok=True)

        overlay = build_overlay_spec(self._settings)
        if self._settings.avatar_overlay_enabled and overlay is None:
            self._log.warning("avatar_overlay_skipped_missing_image",
                              path=self._settings.avatar_image_path)

        _, _, frame_h = self._settings.video_resolution.partition("x")
        subscribe = build_subscribe_spec(
            self._settings, run_root=run_root,
            total_duration=voiceover.duration_sec, frame_height=int(frame_h or 0) or 1080,
        )

        path = self._backend.render(
            segments=resolved,
            audio_path=audio_real,
            captions_path=captions_real,
            citations_path=citations_real,
            output_path=str(out_real),
            resolution=self._settings.video_resolution,
            fps=self._settings.video_fps,
            burn_captions=self._settings.captions_enabled,
            overlay=overlay,
            speed=self._settings.video_speed,
            transition=self._settings.scene_transition,
            transition_sec=self._settings.scene_transition_sec,
            color_warmth=self._settings.color_warmth,
            subscribe=subscribe,
        )

        size = Path(path).stat().st_size if Path(path).exists() else 0
        return VideoAsset(
            run_id=run_id,
            video_path=_VIDEO_REL,
            duration_sec=round(voiceover.duration_sec / (self._settings.video_speed or 1.0), 3),
            resolution=self._settings.video_resolution,
            fps=self._settings.video_fps,
            backend=getattr(self._backend, "name", self._settings.render_backend),
            has_captions=self._settings.captions_enabled,
            has_avatar=overlay is not None,
            file_size_bytes=size,
            provenance=Provenance(
                produced_by="renderer", model=None, config_hash=self._settings.config_hash
            ),
        )
