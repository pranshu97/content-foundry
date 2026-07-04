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


def build_scene_srt(cues: Sequence[tuple[float, float, str]]) -> str:
    """Build an SRT from explicit (start, end, text) cues — e.g. on-screen source citations."""
    out: list[str] = []
    idx = 1
    for start, end, text in cues:
        text = (text or "").strip()
        if not text:
            continue
        # Prepend the ASS \an8 override (top-centre). libass honours inline overrides even in an
        # SRT track, which is far more reliable than the subtitles filter's force_style Alignment.
        out.append(f"{idx}\n{_fmt_ts(start)} --> {_fmt_ts(max(end, start))}\n{{\\an8}}{text}\n")
        idx += 1
    return "\n".join(out)


def write_scene_srt(path: str | Path, cues: Sequence[tuple[float, float, str]]) -> bool:
    """Write a scene-timed SRT; returns False and writes nothing when no cue carries text."""
    content = build_scene_srt(cues)
    if not content:
        return False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return True


def source_only(on_screen_text: str) -> str:
    """Return just the 'Source: …' citation from an on-screen callout; '' when there is none.

    on_screen_text is '<callout> · Source: <name>'; only the source belongs on the burned top strip.
    """
    idx = (on_screen_text or "").lower().rfind("source:")
    return on_screen_text[idx:].strip() if idx >= 0 else ""
