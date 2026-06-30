"""Grounding checks — every statistical claim must tie back to a fetched fact (Ch. 9.3a)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import DataBrief, Script

# A "stat token" = a $ amount, a % figure, or a bare number with >= 2 digits.
# (Single-digit ordinals like "3 steps" are intentionally ignored to avoid false positives.)
STAT_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?%|\b\d{2,}(?:[.,]\d+)*\b")


def extract_stats(text: str) -> list[str]:
    return STAT_RE.findall(text or "")


def _valid_fact_ref(fact_ref: int | None, brief: DataBrief) -> bool:
    return fact_ref is not None and 0 <= fact_ref < len(brief.key_facts)


@dataclass
class GroundingResult:
    score: float  # 0-10 (10 when there are no stats to ground)
    total_stats: int
    grounded_stats: int
    ungrounded: list[tuple[int, str]] = field(default_factory=list)  # (scene_index, token)

    @property
    def fully_grounded(self) -> bool:
        return not self.ungrounded


def check_grounding(script: Script, brief: DataBrief) -> GroundingResult:
    """Score grounding as ``10 * grounded_stats / total_stats`` (Ch. 9.3a)."""
    total = 0
    grounded = 0
    ungrounded: list[tuple[int, str]] = []

    for scene in script.scenes:
        tokens = extract_stats(scene.narration)
        if not tokens:
            continue
        scene_grounded = _valid_fact_ref(scene.fact_ref, brief)
        for token in tokens:
            total += 1
            if scene_grounded:
                grounded += 1
            else:
                ungrounded.append((scene.index, token))

    score = 10.0 if total == 0 else round(10.0 * grounded / total, 2)
    return GroundingResult(
        score=score, total_stats=total, grounded_stats=grounded, ungrounded=ungrounded
    )


def ungrounded_scene_indices(script: Script, brief: DataBrief) -> list[int]:
    """Scenes whose narration contains a stat token but lack a valid ``fact_ref`` (for repair)."""
    out: list[int] = []
    for scene in script.scenes:
        if extract_stats(scene.narration) and not _valid_fact_ref(scene.fact_ref, brief):
            out.append(scene.index)
    return out
