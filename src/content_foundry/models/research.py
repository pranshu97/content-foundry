"""ResearchBrief artifact — Agent 1.5 (Researcher). A source-backed depth report on the run's chosen
idea: the MECHANISMS (how/why things work) the Script Generator draws on to write non-obvious,
insightful scenes. Synthesized by an LLM from real fetched web pages, so every point is grounded."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .provenance import utcnow


class ResearchPoint(BaseModel):
    point: str  # the finding/claim in one sentence
    explanation: str = ""  # HOW and WHY it works — the mechanism, cause, incentive, or psychology
    evidence: str = ""  # a concrete detail, number, or example from the sources
    source_url: str | None = None  # where it came from


class ResearchBrief(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["research"] = "research"
    idea: str = ""  # the chosen video idea this research supports
    points: list[ResearchPoint] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)  # pages actually fetched + read
    used_model: str | None = None  # LLM that synthesized it; None when the deterministic fallback ran
    generated_at: datetime = Field(default_factory=utcnow)
