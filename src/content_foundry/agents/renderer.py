"""Agent 6 — Video Renderer. Deterministic assembly of audio + visuals + captions (Ch. 12)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, VideoAsset, VisualPackage, VoiceoverAsset
from ..production.captions import citation_label, write_scene_srt
from ..production.like_nudge import build_like_spec
from ..production.overlay import build_overlay_spec
from ..production.sound_design import mix_studio_audio
from ..production.subscribe import build_subscribe_spec
from ..production.timeline import build_timeline

_VIDEO_REL = "assets/video.mp4"


def _stat_spoken_at(seg, word_timings) -> float:
    """The moment the scene's statistic is spoken — the first digit-bearing word inside the scene's
    window — so the source citation appears exactly when the data is stated. Falls back to the
    scene's start when the number is spelled out (no digits) or word timings are missing."""
    for w in word_timings:
        if seg.start <= w.start <= seg.end and any(ch.isdigit() for ch in w.word):
            return w.start
    return seg.start


class Renderer:
    def __init__(self, settings, render_backend, sfx_client=None):
        self._settings = settings
        self._backend = render_backend
        self._sfx = sfx_client
        self._log = get_logger(component="renderer")

    def run(
        self, run_id: str, voiceover: VoiceoverAsset, visuals: VisualPackage, *, run_root: Path
    ) -> VideoAsset:
        segments = build_timeline(
            voiceover, visuals, motion_enabled=self._settings.image_motion != "none"
        )
        resolved = [
            replace(
                seg,
                visual_path=str(run_root / seg.visual_path) if seg.visual_path else "",
                clips=tuple((str(run_root / p), d) for p, d in seg.clips),
            )
            for seg in segments
        ]
        audio_real = str(run_root / voiceover.audio_path)
        _, _, frame_h = self._settings.effective_resolution.partition("x")
        subscribe = build_subscribe_spec(
            self._settings,
            run_root=run_root,
            total_duration=voiceover.duration_sec,
            frame_height=int(frame_h or 0) or 1080,
        )
        like = build_like_spec(
            self._settings,
            run_root=run_root,
            total_duration=voiceover.duration_sec,
            frame_height=int(frame_h or 0) or 1080,
        )
        sfx_cues: list[tuple[float, str]] = []
        sfx_active = self._sfx is not None and getattr(self._sfx, "enabled", False)
        if sfx_active:
            sfx_cues = [(seg.start, seg.sfx) for seg in segments if seg.sfx]
            # Ring a bell the instant the Subscribe badge fades in at the midpoint.
            if subscribe is not None and self._settings.subscribe_bell_enabled:
                sfx_cues.append((subscribe.start, self._settings.subscribe_bell_sound))

        prog_sfx_cues: list[tuple[float, str]] = []
        if getattr(self._settings, "programmatic_sfx_enabled", False):
            for seg in segments:
                beat_cursor = seg.start
                g_flags = list(getattr(seg, "is_graphic", ()) or [])
                for j, (_, b_dur) in enumerate(seg.clips):
                    # Skip scene-opening whoosh only when a scene-level SFX already fires there.
                    if j < len(g_flags) and g_flags[j] and not (j == 0 and seg.sfx and sfx_active):
                        prog_sfx_cues.append((beat_cursor, "soft_whoosh" if j == 0 else "ui_tick"))
                    beat_cursor += b_dur

        ambient_on = bool(getattr(self._settings, "enable_ambient_music", False))
        if sfx_cues or prog_sfx_cues or ambient_on:
            # Export lossless PCM WAV so FFmpeg loudnorm/AAC never suffers a 3rd lossy MP3 pass.
            mixed = run_root / "assets/narration_mixed.wav"
            if mix_studio_audio(
                audio_real,
                mixed,
                scene_sfx_cues=sfx_cues,
                sfx_client=self._sfx,
                sfx_gain_db=self._settings.sfx_volume_db,
                # Effects are cued at the scene start, where the voiceover reserved silence for
                # them — keep each one inside that beat so it never runs over the narration.
                sfx_max_len_sec=self._settings.sfx_gap_sec,
                programmatic_sfx_cues=prog_sfx_cues,
                programmatic_sfx_gain_db=getattr(
                    self._settings, "programmatic_sfx_volume_db", -14.0
                ),
                ambient_enabled=ambient_on,
                ambient_gain_db=getattr(self._settings, "ambient_music_volume_db", -30.0),
                ambient_music_path=getattr(self._settings, "ambient_music_path", "") or "",
                work_dir=run_root / "assets" / "_synth_audio",
            ):
                audio_real = str(mixed)

        captions_real = (
            str(run_root / visuals.captions_path)
            if self._settings.effective_captions_enabled
            else None
        )
        citations_rel = "assets/citations.srt"
        # Skip redundant top-strip burned citations on scenes where a programmatic graphic already
        # renders its own crisp bottom-left SOURCE badge.
        graphic_sourced_scenes = {
            sv.scene_index
            for sv in visuals.scenes
            if any(
                getattr(sh, "visual_type", "") == "programmatic_graphic"
                and bool((getattr(sh, "template_props", None) or {}).get("source"))
                for sh in (sv.shots or [])
            )
        }
        # Each source citation appears the instant its statistic is spoken and clears after a brief
        # window (citation_seconds) — a glance, not a whole-scene watermark. Only the source's domain
        # name is shown (no 'Source:' prefix, no .com/.org TLD).
        max_sec = self._settings.citation_seconds
        cues: list[tuple[float, float, str]] = []
        for s in segments:
            if s.index in graphic_sourced_scenes:
                continue
            label = citation_label(s.on_screen_text or "")
            if not label:
                continue
            start = _stat_spoken_at(s, voiceover.word_timings)
            cues.append((start, min(s.end, start + max_sec), label))
        has_citations = write_scene_srt(run_root / citations_rel, cues)
        citations_real = str(run_root / citations_rel) if has_citations else None
        out_real = run_root / _VIDEO_REL
        out_real.parent.mkdir(parents=True, exist_ok=True)

        overlay = build_overlay_spec(self._settings)
        if self._settings.avatar_overlay_enabled and overlay is None:
            self._log.warning(
                "avatar_overlay_skipped_missing_image", path=self._settings.avatar_image_path
            )

        path = self._backend.render(
            segments=resolved,
            audio_path=audio_real,
            captions_path=captions_real,
            citations_path=citations_real,
            output_path=str(out_real),
            resolution=self._settings.effective_resolution,
            fps=self._settings.video_fps,
            burn_captions=self._settings.effective_captions_enabled,
            overlay=overlay,
            speed=self._settings.video_speed,
            transition=self._settings.effective_scene_transition,
            transition_sec=self._settings.scene_transition_sec,
            color_warmth=self._settings.color_warmth,
            subscribe=subscribe,
            like=like,
        )

        # SHORTS: bake the designed thumbnail as a brief FROZEN opening frame so it becomes a real
        # video frame — the only way to control a Short's thumbnail (YouTube ignores a custom one).
        intro_extra = 0.0
        intro_sec = float(getattr(self._settings, "shorts_thumbnail_intro_sec", 0.0) or 0.0)
        if self._settings.is_short and intro_sec > 0 and visuals.thumbnail_path:
            from ..providers.render_backend import prepend_image_intro

            thumb = run_root / visuals.thumbnail_path
            applied = bool(thumb.exists()) and prepend_image_intro(
                video_path=path,
                image_path=str(thumb),
                seconds=intro_sec,
                resolution=self._settings.effective_resolution,
                fps=self._settings.video_fps,
                ffmpeg_path=self._settings.ffmpeg_path,
                video_encoder=self._settings.video_encoder,
            )
            self._log.info("shorts_thumbnail_intro", seconds=intro_sec, applied=bool(applied))
            intro_extra = intro_sec if applied else 0.0

        size = Path(path).stat().st_size if Path(path).exists() else 0
        return VideoAsset(
            run_id=run_id,
            video_path=_VIDEO_REL,
            duration_sec=round(
                voiceover.duration_sec / (self._settings.video_speed or 1.0) + intro_extra, 3
            ),
            resolution=self._settings.effective_resolution,
            fps=self._settings.video_fps,
            backend=getattr(self._backend, "name", self._settings.render_backend),
            has_captions=self._settings.effective_captions_enabled,
            has_avatar=overlay is not None,
            file_size_bytes=size,
            provenance=Provenance(
                produced_by="renderer", model=None, config_hash=self._settings.config_hash
            ),
        )
