"""Time-boxing helpers — year-stamp titles and keep ideas evergreen/reusable (future plan 3).

Deterministic, no LLM. A time-boxed title ("Best Career Advice in 2026") earns freshness signals
and seasonal search traffic, while the underlying script stays evergreen so the same idea can be
refreshed and re-published the following year.
"""

from __future__ import annotations

import re

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def has_year(text: str) -> bool:
    """True when the text already contains an explicit 4-digit year (19xx/20xx)."""
    return bool(_YEAR_RE.search(text or ""))


def timebox_title(title: str, year: int) -> str:
    """Append ``(<year>)`` when the title has no explicit year. Idempotent and deterministic."""
    title = (title or "").strip()
    if not title or has_year(title):
        return title
    return f"{title} ({year})"


def build_time_context(year: int) -> str:
    """Prompt clause instructing the writer to time-box the title yet keep advice evergreen."""
    return (
        f"TIME CONTEXT: The current year is {year}. When it strengthens the title, time-box it "
        f"(e.g. 'in {year}'). Keep the core advice evergreen so the same idea can be refreshed and "
        "re-published next year."
    )
