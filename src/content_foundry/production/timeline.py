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


def build_timeline(voiceover: VoiceoverAsset, visuals: VisualPackage) -> list[RenderSegment]:
    """Lock visuals to audio: each scene's duration comes from ``VoiceoverAsset.scene_timings``."""
    by_scene = {sv.scene_index: sv for sv in visuals.scenes}
    segments: list[RenderSegment] = []
    for timing in sorted(voiceover.scene_timings, key=lambda s: s.scene_index):
        visual = by_scene.get(timing.scene_index)
        segments.append(
            RenderSegment(
                index=timing.scene_index,
                start=timing.start,
                end=timing.end,
                duration=max(0.0, timing.end - timing.start),
                visual_path=visual.path if visual else "",
                visual_kind=visual.kind if visual else "card",
                on_screen_text=visual.on_screen_text if visual else None,
            )
        )
    return segments
