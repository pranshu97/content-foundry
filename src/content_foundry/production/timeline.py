"""Map approved scenes to timed media segments for the renderer (Ch. 12.3)."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import VisualPackage, VoiceoverAsset
from .motion import NONE, pick_motion

_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


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
    # Camera move per entry in ``clips``. Stills get one so they don't sit frozen between moving
    # B-roll; real footage already moves, so it is always "none" there.
    motions: tuple[str, ...] = ()


def _shot_durations(shots, word_starts: list[float], start: float, end: float) -> list[float]:
    """Seconds per shot, cut where the words each shot illustrates are ACTUALLY spoken.

    Splitting the scene's span evenly looks right but is not: the span begins with the silence the
    voiceover reserves for the opening beat / sound effect, so an even split hands part of that
    silence to every shot and each image lands BEFORE the words it belongs to (measured at a full
    1.0s early on the first shot of every scene, decaying to ~0.25s by the last). The image director
    slices the narration by word count, so cutting on the matching word's real timestamp is what puts
    the two back in step. Shot 0 keeps the reserved silence so a picture is up from the first frame.

    Falls back to the even split when the voice has no usable word timings for this scene.
    """
    span = max(0.0, end - start)
    weights = [max(getattr(s, "duration_sec", 0.0), 0.0) for s in shots]
    total = sum(weights) or 1.0
    even = [span * w / total for w in weights]
    if len(word_starts) < len(shots) or not span:
        return even
    per = len(word_starts) / len(shots)
    # Each shot begins on its first word; the first begins at the scene edge, covering the silence.
    edges = [start] + [word_starts[round(j * per)] for j in range(1, len(shots))] + [end]
    durations = [max(0.0, edges[j + 1] - edges[j]) for j in range(len(shots))]
    # A pathological timing set (out of order, or all in one instant) must never produce a zero-length
    # or negative beat — that would drop a shot from the render entirely.
    return durations if all(d > 0.05 for d in durations) else even


def build_timeline(
    voiceover: VoiceoverAsset, visuals: VisualPackage, *, motion_enabled: bool = True
) -> list[RenderSegment]:
    """Lock visuals to audio: each scene's duration comes from ``VoiceoverAsset.scene_timings``."""
    by_scene = {sv.scene_index: sv for sv in visuals.scenes}
    segments: list[RenderSegment] = []
    previous_motion = ""
    for timing in sorted(voiceover.scene_timings, key=lambda s: s.scene_index):
        visual = by_scene.get(timing.scene_index)
        scene_dur = max(0.0, timing.end - timing.start)
        clips: tuple[tuple[str, float], ...] = ()
        motions: tuple[str, ...] = ()
        if visual and visual.shots:
            spoken = [
                w.start for w in voiceover.word_timings if timing.start <= w.start < timing.end
            ]
            durations = _shot_durations(visual.shots, spoken, timing.start, timing.end)
            clips = tuple((s.path, dur) for s, dur in zip(visual.shots, durations, strict=True))
            picked: list[str] = []
            for i, shot in enumerate(visual.shots):
                still = shot.path.lower().endswith(_STILL_SUFFIXES)
                if not (motion_enabled and still):
                    picked.append(NONE)
                    continue
                # The prompt states the camera the image was composed for, so the move can match it.
                move = pick_motion(
                    shot.prompt or shot.query or "", index=i, previous=previous_motion
                )
                picked.append(move)
                previous_motion = move
            motions = tuple(picked)
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
                motions=motions,
            )
        )
    return segments
