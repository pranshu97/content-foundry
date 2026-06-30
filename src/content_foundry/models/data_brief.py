"""DataBrief artifact — the grounded, citation-ready output of Agent 1 (Ch. 7.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .provenance import Provenance, utcnow


class Citation(BaseModel):
    source: str  # adzuna | layoffs | news | bls
    url: str | None = None
    observed_at: datetime
    snippet: str  # exact normalized signal text supporting the fact


class KeyFact(BaseModel):
    statement: str
    metric: str | None = None
    value: str | None = None
    citation: Citation  # MUST reference a real fetched signal


class ContentAngle(BaseModel):
    hook: str
    supporting_fact_ids: list[int] = Field(default_factory=list)
    why_nonobvious: str


class DataBrief(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["data_brief"] = "data_brief"
    niche: str
    topic_seed: str | None = None
    key_facts: list[KeyFact] = Field(default_factory=list)
    content_angles: list[ContentAngle] = Field(default_factory=list)
    coverage: dict[str, bool] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utcnow)
    provenance: Provenance
