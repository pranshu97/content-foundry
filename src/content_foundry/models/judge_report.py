"""JudgeReport artifact + Verdict enum — output of the quality gate (Ch. 9.6)."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .provenance import Provenance


class Verdict(str, Enum):
    PASS = "PASS"
    REVISE = "REVISE"
    FAIL = "FAIL"


class DimensionScore(BaseModel):
    dimension: str
    score_1_5: int | None = None  # LLM-scored dims: discrete 1-5 (None for code-only dims)
    score: float  # normalized 0-10
    weight: float
    minimum: float | None = None  # hard floor on the 0-10 scale
    passed: bool
    evidence: str | None = None  # quoted span(s) from the script (LLM-scored dims)
    justification: str
    fix_suggestion: str | None = None


class JudgeReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["judge_report"] = "judge_report"
    attempt_number: int
    template_id: str
    scores: list[DimensionScore] = Field(default_factory=list)
    weighted_total: float
    insight_score: float
    grounding_score: float
    template_fatigue: bool = False
    force_shift: bool = False
    forced_template_id: str | None = None
    verdict: Verdict
    summary: str
    revision_instructions: str | None = None
    provenance: Provenance
