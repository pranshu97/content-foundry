"""Compliance & grounding safeguards."""

from __future__ import annotations

from .disclosure import (
    DISCLOSURE_SENTENCE,
    description_has_disclosure,
    disclosure_checklist,
    ensure_description_discloses,
    resolve_publish_outcome,
)
from .grounding import (
    GroundingResult,
    check_grounding,
    extract_stats,
    ungrounded_scene_indices,
)

__all__ = [
    "DISCLOSURE_SENTENCE",
    "description_has_disclosure",
    "ensure_description_discloses",
    "resolve_publish_outcome",
    "disclosure_checklist",
    "GroundingResult",
    "check_grounding",
    "extract_stats",
    "ungrounded_scene_indices",
]
