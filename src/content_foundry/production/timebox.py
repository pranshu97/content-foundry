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
    """Prompt clause: the writer sets the ``time_sensitive`` flag; the year is stamped on the TITLE
    only then, and even then the year is used SPARINGLY — only where it genuinely applies, never
    sprinkled through the script."""
    return (
        f"TIME CONTEXT: The current year is {year}. Set \"time_sensitive\": true ONLY when the topic "
        f"is genuinely time-bound — trends, rankings, salaries, what's new, or anything that dates, "
        f"INCLUDING how/why/what questions about the current moment (e.g. 'How to get hired in "
        f"{year}', 'Why learn AI in {year}'). Set it false for EVERGREEN explainers whose answer does "
        f"not change with the year (e.g. 'How recommendation engines work'), and keep the underlying "
        "advice evergreen either way. "
        f"USE THE YEAR SPARINGLY — only where it truly applies: even when time_sensitive, name {year} "
        "AT MOST ONCE in the whole script, in the hook or a single freshness beat where it adds real "
        "meaning. Do NOT repeat it scene after scene, and do NOT tack it onto the CTA, description, "
        f"tags, or thumbnail UNLESS the year genuinely IS the point there (e.g. a '{year} salaries' "
        "reveal). A script that name-drops the year every few sentences reads dated, robotic, and "
        "keyword-stuffed — write everything else timelessly. "
        "When you leave the year OUT of a sentence that could have carried it, REWRITE that sentence "
        "so it still reads naturally — never leave a dangling \"In ,\" or a blank gap where the year "
        "would have gone."
    )
