"""Time context for the writer — keep videos evergreen, date them only when genuinely warranted.

Deterministic, no LLM. Titles are NOT mechanically year-stamped: a bolted-on "(2026)" dates an
otherwise timeless video (the recurring complaint that the year appeared on every title). Instead
the writer is told to keep everything evergreen and to name a year only when the topic genuinely is
a specific-year ranking, salary, or trend, so the same idea can be refreshed and re-published in a
later year.
"""

from __future__ import annotations


def build_time_context(year: int) -> str:
    """Prompt clause: keep the video EVERGREEN. A year is named only where it is genuinely the point
    (a specific-year ranking, salary, or trend), at most once, and woven in naturally — never
    mechanically stamped onto an otherwise timeless title."""
    return (
        f"TIME CONTEXT: The current year is {year}, but write for a viewer watching in ANY year. "
        "Most topics are EVERGREEN — a skill, an interview, how something works, a mindset — and "
        "their answer does not change with the year; keep those completely timeless. Set "
        '"time_sensitive": true ONLY when the topic is genuinely dated — this year\'s rankings, '
        "current salary figures, what is NEW right now, or a trend that will look stale next year — "
        "and false for everything else (a how/why/what explainer or interview-prep topic is "
        "EVERGREEN even when its subject is currently popular). "
        f"Do NOT put {year} (or any year) in the title, thumbnail, tags, description, or narration "
        "UNLESS time_sensitive is true AND the year genuinely IS the point. When it is, name the "
        "year AT MOST ONCE, woven naturally where it belongs (the title of a "
        f"'{year} salaries' reveal, or a single freshness beat) — never tack a bare '({year})' onto "
        "an evergreen title, and never repeat the year scene after scene. If you leave the year out "
        "of a sentence that could have carried it, rewrite it so it still reads naturally — never "
        'leave a dangling "In ,".'
    )
