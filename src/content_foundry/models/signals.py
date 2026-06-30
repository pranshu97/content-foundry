"""Raw and normalized data signals (Ch. 7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .provenance import utcnow

# Canonical signal kinds the deterministic distiller understands.
SIGNAL_KINDS = ("salary", "posting_trend", "layoff", "news", "outlook")


class RawSignal(BaseModel):
    """An untransformed payload as returned by a single :class:`DataSource`."""

    source: str
    payload: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=utcnow)


class NormalizedSignal(BaseModel):
    """A source-agnostic, comparable signal used by ranking and distillation."""

    source: str  # adzuna | layoffs | news | bls
    kind: str  # one of SIGNAL_KINDS
    title: str
    value: str | None = None
    unit: str | None = None
    observed_at: datetime
    url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
