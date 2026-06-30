"""Build a styled ``.srt`` captions track from word-level timings (Ch. 11.3)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..models import WordTiming


def _fmt_ts(seconds: float) -> str:
    """Format seconds as an SRT timestamp ``HH:MM:SS,mmm``."""
    ms_total = int(round(max(0.0, seconds) * 1000))
    hours, ms_total = divmod(ms_total, 3_600_000)
    minutes, ms_total = divmod(ms_total, 60_000)
    secs, millis = divmod(ms_total, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(word_timings: Sequence[WordTiming], max_words: int = 7) -> str:
    """Group word timings into ≤``max_words`` cues; never overlapping (timings are monotonic)."""
    cues: list[str] = []
    index = 1
    for start in range(0, len(word_timings), max_words):
        group = word_timings[start : start + max_words]
        if not group:
            continue
        cue_start = group[0].start
        cue_end = max(group[-1].end, cue_start)
        text = " ".join(w.word for w in group)
        cues.append(f"{index}\n{_fmt_ts(cue_start)} --> {_fmt_ts(cue_end)}\n{text}\n")
        index += 1
    return "\n".join(cues)


def write_srt(path: str | Path, word_timings: Sequence[WordTiming], max_words: int = 7) -> str:
    content = build_srt(word_timings, max_words=max_words)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)
