"""IdeaSelection artifact — records the ideas the Brainstormer generated and the exact one the run
went ahead with, so a run's creative choice is inspectable after the fact (Ch. 14)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .provenance import utcnow


class IdeaSelection(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["ideas"] = "ideas"
    seed: str = ""  # the operator's --idea / focus that steered the brainstorm (may be blank)
    brainstorm_enabled: bool = True
    # brainstorm = picked from a generated idea; seed = brainstorm off / no ideas, raw --idea used;
    # custom = the interactive chooser returned an idea the operator typed in themselves.
    source: Literal["brainstorm", "seed", "custom"] = "brainstorm"
    generated: list[str] = Field(default_factory=list)  # every idea proposed this run
    chosen: str = ""  # the exact idea the run committed to
    chosen_index: int = -1  # index into ``generated``, or -1 when the pick is not from that list
    generated_at: datetime = Field(default_factory=utcnow)
