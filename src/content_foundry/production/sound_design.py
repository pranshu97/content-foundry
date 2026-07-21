"""Sound design: mix script sound-effect cues into the narration at their scene-start times (Ch. 12.4)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


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


def mix_sfx(
    narration_path: str,
    cues: Sequence[tuple[float, str]],
    sfx_client,
    out_path: str | Path,
    *,
    gain_db: float = -8.0,
) -> bool:
    """Overlay each resolved SFX onto the narration at its ``start`` (seconds) and write ``out_path``.

    ``gain_db`` is applied RELATIVE TO THE NARRATION's loudness (e.g. -8 => each effect sits 8 dB under
    the voice), so a loud stock clip is tamed to a predictable level and lowering the value reliably
    makes effects quieter. Returns True when the mixed file was written (at least one cue resolved to a
    real clip), else False so the caller keeps the original narration. A bad clip is skipped, never
    fatal.
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
        base_dbfs = base.dBFS  # the voice's loudness -> every SFX is set RELATIVE to it (not its own)
        for start, path in resolved:
            try:
                sfx = AudioSegment.from_file(path)
                sfx = sfx.apply_gain(_relative_gain(base_dbfs, sfx.dBFS, gain_db))
            except Exception:  # pragma: no cover - a bad clip must not kill the render
                continue
            base = base.overlay(sfx, position=int(start * 1000))
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        base.export(str(out), format="mp3")
    except Exception:
        # Mixing must NEVER break the render (e.g. an undecodable narration) — fall back to the
        # untouched narration by reporting failure to the caller.
        return False
    return True
