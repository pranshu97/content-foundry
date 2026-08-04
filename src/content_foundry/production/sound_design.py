"""Sound design: mix script sound-effect cues into the narration at their scene-start times (Ch. 12.4)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Two effects must never sound at once (a scene cue and the subscribe bell can land on the same
# beat) — the stack reads as one muddy hit instead of two intents. A colliding effect is pushed to
# just after the previous one ends, with this much air between them.
_SFX_MIN_GAP_SEC = 0.15
# ...but only so far. Past this an effect no longer reads as *that* moment's punctuation, so it is
# dropped instead of firing at the wrong line.
_SFX_MAX_SHIFT_SEC = 2.0
# Fade applied when an effect has to be trimmed, so it tapers instead of cutting off mid-sound.
_SFX_FADE_MS = 120


def _relative_gain(base_dbfs: float, sfx_dbfs: float, gain_db: float) -> float:
    """dB delta to apply to an SFX so it lands ``gain_db`` dB relative to the NARRATION's loudness
    (``base_dbfs``). Returns 0.0 for a silent/undecodable narration or SFX (a non-finite dBFS) so the
    clip is left alone. This makes SFX_VOLUME_DB behave as documented — relative to the voice — so a
    loud stock clip no longer stays loud and LOWERING the value reliably makes every effect quieter,
    regardless of each clip's own baked-in level."""
    import math

    if not math.isfinite(sfx_dbfs) or not math.isfinite(base_dbfs):
        return 0.0
    return (base_dbfs + gain_db) - sfx_dbfs


def plan_sfx_positions(cues: Sequence[tuple[float, float]]) -> list[float | None]:
    """Place effects so they sound ONE AT A TIME. ``cues`` is ``(start_sec, duration_sec)`` ordered by
    start; returns the position for each cue, or ``None`` for one that must be dropped.

    Effects still play UNDER the narration (that's what an effect is) but never under EACH OTHER: two
    that land on the same beat — typically a scene cue and the subscribe bell — stack into one muddy
    hit that reads as neither. The later one waits for the earlier to finish, unless that would drag
    it more than ``_SFX_MAX_SHIFT_SEC`` from its own moment, in which case it is dropped rather than
    fired against the wrong line.
    """
    positions: list[float | None] = []
    busy_until = 0.0  # when the previously placed effect finishes
    for start, duration in cues:
        position = max(start, busy_until)
        if position - start > _SFX_MAX_SHIFT_SEC:
            positions.append(None)
            continue
        positions.append(position)
        busy_until = position + max(0.0, duration) + _SFX_MIN_GAP_SEC
    return positions


def mix_sfx(
    narration_path: str,
    cues: Sequence[tuple[float, str]],
    sfx_client,
    out_path: str | Path,
    *,
    gain_db: float = -8.0,
    max_len_sec: float = 0.0,
) -> bool:
    """Overlay each resolved SFX onto the narration at its ``start`` (seconds) and write ``out_path``.

    ``gain_db`` is applied RELATIVE TO THE NARRATION's loudness (e.g. -8 => each effect sits 8 dB under
    the voice), so a loud stock clip is tamed to a predictable level and lowering the value reliably
    makes effects quieter. ``max_len_sec`` is the silent beat the voiceover reserved ahead of the
    narration for this effect: anything longer is trimmed (with a short fade) so a 3 s stock clip
    can't run past the gap and play over the first words. Returns True when the mixed file was written
    (at least one cue resolved to a real clip), else False so the caller keeps the original narration.
    A bad clip is skipped, never fatal.
    """
    resolved: list[tuple[float, str]] = []
    for start, keyword in cues:
        path = sfx_client.resolve(keyword) if keyword else None
        if path:
            resolved.append((max(0.0, float(start)), path))
    if not resolved:
        return False
    try:
        from pydub import AudioSegment  # lazy: optional dependency
    except ImportError:  # pragma: no cover - pydub optional
        return False

    try:
        base = AudioSegment.from_file(narration_path)
        base_dbfs = (
            base.dBFS
        )  # the voice's loudness -> every SFX is set RELATIVE to it (not its own)
        loaded: list[tuple[float, Any]] = []
        for start, path in sorted(resolved, key=lambda c: c[0]):
            try:
                sfx = AudioSegment.from_file(path)
                sfx = sfx.apply_gain(_relative_gain(base_dbfs, sfx.dBFS, gain_db))
                # Keep the effect inside the silence reserved for it, fading rather than hard-cutting.
                if max_len_sec > 0 and len(sfx) > max_len_sec * 1000:
                    sfx = sfx[: int(max_len_sec * 1000)].fade_out(_SFX_FADE_MS)
            except Exception:  # pragma: no cover - a bad clip must not kill the render
                continue
            loaded.append((start, sfx))
        positions = plan_sfx_positions([(start, len(sfx) / 1000.0) for start, sfx in loaded])
        for (_, sfx), position in zip(loaded, positions, strict=True):
            if position is None:
                continue
            base = base.overlay(sfx, position=int(position * 1000))
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        base.export(str(out), format="mp3")
    except Exception:
        # Mixing must NEVER break the render (e.g. an undecodable narration) — fall back to the
        # untouched narration by reporting failure to the caller.
        return False
    return True
