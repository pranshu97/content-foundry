"""Map approved scenes to timed media segments for the renderer (Ch. 12.3)."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import VisualPackage, VoiceoverAsset


@dataclass(frozen=True)
class RenderSegment:
    index: int
    start: float
    end: float
    duration: float
    visual_path: str
    visual_kind: str  # image | broll | card
    on_screen_text: str | None = None
    sfx: str | None = None
    clips: tuple[tuple[str, float], ...] = ()  # ordered (path, seconds) beats; empty => single clip


def build_timeline(voiceover: VoiceoverAsset, visuals: VisualPackage) -> list[RenderSegment]:
    """Lock visuals to audio: each scene's duration comes from ``VoiceoverAsset.scene_timings``."""
    by_scene = {sv.scene_index: sv for sv in visuals.scenes}
    segments: list[RenderSegment] = []
    for timing in sorted(voiceover.scene_timings, key=lambda s: s.scene_index):
        visual = by_scene.get(timing.scene_index)
        scene_dur = max(0.0, timing.end - timing.start)
        clips: tuple[tuple[str, float], ...] = ()
        if visual and visual.shots:
            # Rescale the shots to fill exactly the scene's real (audio-locked) duration.
            total = sum(max(s.duration_sec, 0.0) for s in visual.shots) or 1.0
            clips = tuple((s.path, scene_dur * max(s.duration_sec, 0.0) / total) for s in visual.shots)
        segments.append(
            RenderSegment(
                index=timing.scene_index,
                start=timing.start,
                end=timing.end,
                duration=scene_dur,
                visual_path=visual.path if visual else "",
                visual_kind=visual.kind if visual else "card",
                on_screen_text=visual.on_screen_text if visual else None,
                sfx=visual.sfx if visual else None,
                clips=clips,
            )
        )
    return segments
