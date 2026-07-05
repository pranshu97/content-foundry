"""Sound design: mix script sound-effect cues into the narration at their scene-start times (Ch. 12.4)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


def mix_sfx(
    narration_path: str,
    cues: Sequence[tuple[float, str]],
    sfx_client,
    out_path: str | Path,
    *,
    gain_db: float = -8.0,
) -> bool:
    """Overlay each resolved SFX onto the narration at its ``start`` (seconds) and write ``out_path``.

    Returns True when the mixed file was written (at least one cue resolved to a real clip), else
    False so the caller keeps the original narration. A bad clip is skipped, never fatal.
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
        for start, path in resolved:
            try:
                sfx = AudioSegment.from_file(path) + gain_db
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
