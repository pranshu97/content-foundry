"""Creator-supplied data (``--data``) — the operator's OWN material, weighed above researched facts.

The CLI value is either literal text or a path to a text file. Either way it becomes one
:class:`KeyFact` per line, so the Script Generator can cite each item individually via ``fact_ref``
and the Judge scores against the very same numbered list.

A line MAY name its own source::

    Churn fell 12% | Q3 board deck
    Referrals drive 40% of hires (source: internal ATS export)
    Median offer rose 4% -- source: https://example.com/report

When a line names no source the fact simply carries none: nothing is invented, and the on-screen
source stamp is skipped for that point rather than showing a placeholder.

These facts are deliberately NOT passed through ``topic_relevance.filter_to_idea``: the creator chose
them by hand, so they are trusted as-is.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..errors import ConfigError
from ..models import Citation, KeyFact, utcnow

# ``--data`` is raw operator input, so it is capped before it can reach a prompt — a pasted book would
# otherwise overflow the model's context window and silently break generation.
MAX_ITEMS = 40
MAX_CHARS = 600

# Leading "-", "*", "•" or "1." list markers from pasted notes. The trailing \s+ matters: it keeps a
# negative number like "-5% churn" intact.
_BULLET = re.compile(r"^\s*(?:[-*\u2022\u2013\u2014]|\d+[.)])\s+")
_TEXT_SUFFIXES = (".txt", ".md", ".text", ".csv")

# The three ways a creator may attach a source. Each REQUIRES a bracket, dash or pipe separator, so an
# ordinary sentence like "our main source: referrals" is left alone instead of being split apart. The
# \b after the keyword stops "sourced from ..." or "Vialink" from being read as a label.
_BRACKET_SOURCE = re.compile(
    r"\s*[(\[]\s*(?:source|src|via)\b\s*[:=]?\s*(?P<src>[^)\]]+?)\s*[)\]]\s*$", re.IGNORECASE
)
_DASH_SOURCE = re.compile(
    r"\s+(?:\u2014|\u2013|--)\s*(?:source|src|via)\b\s*[:=]?\s*(?P<src>.+?)\s*$", re.IGNORECASE
)
_PIPE_SOURCE = re.compile(
    r"\s*\|\s*(?:(?:source|src|via)\b\s*[:=]?\s*)?(?P<src>[^|]+?)\s*$", re.IGNORECASE
)


def resolve_data(value: str | None) -> str:
    """The creator's data as raw text: the file's contents when ``value`` is a path, else ``value``.

    A value that clearly NAMES a text file but cannot be read raises instead of silently degrading
    into a literal "fact" that is just a filename — a mistyped path is the likeliest mistake here.
    """
    text = (value or "").strip()
    if not text or "\n" in text:  # multi-line input is unambiguously literal text
        return text
    names_a_file = text.lower().endswith(_TEXT_SUFFIXES)
    try:
        path = Path(text).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, ValueError) as exc:  # unreadable, or not a usable path at all
        if names_a_file:
            raise ConfigError(f"--data could not read the file {text!r}: {exc}") from exc
        return text
    if names_a_file:
        raise ConfigError(f"--data looks like a file, but nothing exists at {text!r}")
    return text


def split_source(line: str) -> tuple[str, str]:
    """``(statement, source)`` for one data line; ``source`` is ``""`` when the line names none."""
    for pattern in (_BRACKET_SOURCE, _DASH_SOURCE, _PIPE_SOURCE):
        match = pattern.search(line)
        if not match:
            continue
        statement, source = line[: match.start()].strip(), match.group("src").strip()
        if statement and source:
            return statement, source
    return line.strip(), ""


def creator_key_facts(text: str) -> list[KeyFact]:
    """One citable :class:`KeyFact` per non-empty line of the creator's data (capped).

    ``creator_supplied`` is what tells the Script Generator to weigh these slightly above researched
    facts — the citation carries only the source the creator actually gave, never an invented one.
    """
    observed = utcnow()
    facts: list[KeyFact] = []
    for raw_line in (text or "").splitlines():
        statement, source = split_source(_BULLET.sub("", raw_line).strip())
        statement = statement[:MAX_CHARS].strip()
        if not statement:
            continue
        facts.append(
            KeyFact(
                statement=statement,
                creator_supplied=True,
                citation=Citation(
                    source=source,
                    url=source if source.lower().startswith(("http://", "https://")) else None,
                    observed_at=observed,
                    snippet=statement,
                ),
            )
        )
        if len(facts) >= MAX_ITEMS:
            break
    return facts
