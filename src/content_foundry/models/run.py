"""Run / Attempt domain models, run-state enum, and the orchestrator's RunResult (Ch. 2.4, 14)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from .judge_report import Verdict
from .provenance import utcnow


class RunState(str, Enum):
    CREATED = "CREATED"
    FETCHED = "FETCHED"
    GENERATED = "GENERATED"
    JUDGED = "JUDGED"
    APPROVED = "APPROVED"
    REVISING = "REVISING"
    VOICED = "VOICED"
    VISUALIZED = "VISUALIZED"
    RENDERED = "RENDERED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class Attempt(BaseModel):
    attempt_id: str
    run_id: str
    attempt_number: int  # 1-based
    template_id: str
    forced_shift: bool = False
    verdict: Verdict | None = None
    insight_score: float | None = None
    weighted_total: float | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Run(BaseModel):
    run_id: str
    topic_seed: str | None = None
    niche: str | None = None
    state: RunState = RunState.CREATED
    final_verdict: Verdict | None = None
    approved_attempt_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class RunResult(BaseModel):
    """Returned by :func:`run_pipeline` — a summary of the end-to-end effort."""

    run_id: str
    final_state: RunState
    verdict: Verdict | None = None
    from_stage: str
    to_stage: str
    artifacts: dict[str, str] = Field(default_factory=dict)  # stage -> artifact path
    video_url: str | None = None
    package_path: str | None = None
    duration_sec: float | None = None
