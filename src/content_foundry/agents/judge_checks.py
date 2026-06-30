"""Deterministic rubric checks + heuristics for the Judge (Ch. 9.3a, 9.4)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..models import Script
from ..safeguards.disclosure import description_has_disclosure
from ..safeguards.grounding import extract_stats

GENERIC_PHRASES = (
    "network more",
    "update your resume",
    "work hard",
    "stay positive",
    "be yourself",
    "follow your passion",
    "just apply",
    "believe in yourself",
    "hustle",
    "think outside the box",
)
_CAP_TERM_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
_STEP_CUE_RE = re.compile(r"\b(step|first|second|third|next|then|start by|do this|try|use)\b", re.I)
_NONOBVIOUS_RE = re.compile(
    r"\b(counterintuitive|surprising|actually|contrary|reframe|hidden|overlooked|myth)\b", re.I
)


def all_text(script: Script) -> str:
    return script.hook + " " + " ".join(s.narration for s in script.scenes)


def generic_hits(script: Script) -> int:
    text = all_text(script).lower()
    return sum(text.count(phrase) for phrase in GENERIC_PHRASES)


def specificity_score(script: Script) -> float:
    text = all_text(script)
    stats = len(extract_stats(text))
    proper = len(_CAP_TERM_RE.findall(text))
    raw = 2.0 + 1.3 * stats + 0.12 * proper - 1.0 * generic_hits(script)
    return _clamp(raw)


def hook_score(script: Script) -> float:
    hook = script.hook or (script.scenes[0].narration if script.scenes else "")
    words = len(hook.split())
    score = 4.0
    if extract_stats(hook):
        score += 4.0
    if words <= 25:
        score += 2.0
    elif words <= 40:
        score += 1.0
    return _clamp(score)


def _shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if len(tokens) < n:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def hook_similarity(hook: str, recent_hooks: Sequence[str]) -> float:
    target = _shingles(hook)
    return max((_jaccard(target, _shingles(h)) for h in recent_hooks), default=0.0)


@dataclass
class FreshnessResult:
    score: float
    fatigue: bool
    similarity: float


def freshness_and_fatigue(
    template_id: str,
    hook: str,
    recent_template_ids: Sequence[str],
    recent_hooks: Sequence[str],
    *,
    jaccard_threshold: float = 0.6,
) -> FreshnessResult:
    fatigue = bool(recent_template_ids) and recent_template_ids[0] == template_id
    similarity = hook_similarity(hook, recent_hooks)
    if similarity >= jaccard_threshold:
        fatigue = True

    score = 10.0 - 3.0 * recent_template_ids.count(template_id) - 6.0 * similarity
    return FreshnessResult(score=_clamp(score), fatigue=fatigue, similarity=round(similarity, 3))


def compliance_check(script: Script) -> tuple[float, bool]:
    ok = bool(script.synthetic_disclosure) and description_has_disclosure(script.description)
    return (10.0 if ok else 0.0), ok


def heuristic_actionability(script: Script) -> float:
    text = all_text(script)
    steps = len(_STEP_CUE_RE.findall(text))
    stats = len(extract_stats(text))
    raw = 3.0 + 1.0 * min(steps, 4) + 0.6 * min(stats, 5) - 1.5 * generic_hits(script)
    return _clamp(raw)


def heuristic_insight(script: Script) -> float:
    text = all_text(script)
    stats = len(extract_stats(text))
    nonobvious = len(_NONOBVIOUS_RE.findall(text))
    raw = 2.5 + 0.8 * min(stats, 5) + 1.2 * min(nonobvious, 3) - 2.0 * generic_hits(script)
    return _clamp(raw)


def _clamp(value: float, low: float = 0.0, high: float = 10.0) -> float:
    return round(max(low, min(high, value)), 2)
