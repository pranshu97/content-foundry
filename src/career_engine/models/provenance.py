"""Shared provenance block stamped into every artifact (Ch. 19.2)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Timezone-aware UTC now (used as the default for every artifact timestamp)."""
    return datetime.now(UTC)


class Provenance(BaseModel):
    """Auditable record of what produced an artifact, when, and from which inputs.

    ``produced_by`` is the agent name, or ``"operator_edited"`` when an operator hand-edits
    an artifact between stages (detected via a content-hash mismatch on load).
    """

    produced_by: str
    model: str | None = None
    config_hash: str | None = None
    input_hashes: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    schema_version: str = "1.0"
