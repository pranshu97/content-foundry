"""Deterministic distillation: NormalizedSignal -> KeyFact / ContentAngle (Ch. 7.3).

NO LLM. Every field is copied from real signal data, so invented numbers are impossible.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..models import Citation, ContentAngle, KeyFact, NormalizedSignal

# Per-kind statement templates (rendered purely from structured signal fields).
_STATEMENT_TEMPLATES = {
    "salary": "{title} pays a median of {value} {unit}.",
    "posting_trend": "{title}: {value} {unit}.",
    "layoff": "{title}",
    "news": "{title}",
    "outlook": "{title} stands at {value} {unit}.",
}

# Per-kind content-angle hooks, filled with concrete numbers.
_ANGLE_TEMPLATES = {
    "salary": (
        "What {title} really pays now ({value}) — and who is actually getting it.",
        "Most salary advice ignores the real {value} number for {title}.",
    ),
    "posting_trend": (
        "The hiring signal nobody mentions: {value} {unit}.",
        "Why '{title}' ({value} {unit}) changes your job-search math.",
    ),
    "layoff": (
        "{title} — what these layoffs actually signal for your field.",
        "The layoff headline hides the real career move to make.",
    ),
    "news": (
        "{title} — the part that affects your paycheck.",
        "Behind the headline '{title}': the non-obvious takeaway.",
    ),
    "outlook": (
        "{title} is {value} {unit} — here is what that means for you.",
        "The outlook number ({value} {unit}) most channels get wrong.",
    ),
}


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _snippet(signal: NormalizedSignal) -> str:
    raw_snippet = signal.raw.get("snippet") if isinstance(signal.raw, dict) else None
    if raw_snippet:
        return str(raw_snippet)
    parts = [signal.title]
    if signal.value:
        parts.append(f"{signal.value}{(' ' + signal.unit) if signal.unit else ''}")
    return ": ".join(p for p in parts if p)


def build_key_fact(signal: NormalizedSignal) -> KeyFact:
    template = _STATEMENT_TEMPLATES.get(signal.kind, "{title}")
    statement = template.format(
        title=_clean(signal.title), value=_clean(signal.value), unit=_clean(signal.unit)
    ).strip()
    statement = re.sub(r"\s+", " ", statement).strip(": ").strip() + ("" if statement.endswith(".") else "")
    return KeyFact(
        statement=statement,
        metric=signal.kind,
        value=signal.value,
        citation=Citation(
            source=signal.source,
            url=signal.url,
            observed_at=signal.observed_at,
            snippet=_snippet(signal),
        ),
    )


def build_key_facts(signals: Sequence[NormalizedSignal], *, limit: int = 8) -> list[KeyFact]:
    return [build_key_fact(s) for s in signals[:limit]]


def build_angles(
    signals: Sequence[NormalizedSignal], *, limit: int = 3
) -> list[ContentAngle]:
    angles: list[ContentAngle] = []
    for idx, signal in enumerate(signals):
        if len(angles) >= limit:
            break
        templates = _ANGLE_TEMPLATES.get(signal.kind)
        if not templates:
            continue
        hook = templates[idx % len(templates)].format(
            title=_clean(signal.title), value=_clean(signal.value), unit=_clean(signal.unit)
        )
        hook = re.sub(r"\s+", " ", hook).strip()
        angles.append(
            ContentAngle(
                hook=hook,
                supporting_fact_ids=[idx],
                why_nonobvious=(
                    "Built directly from fetched data, not generic advice; "
                    f"cites a concrete {signal.kind} signal from {signal.source}."
                ),
            )
        )
    return angles
