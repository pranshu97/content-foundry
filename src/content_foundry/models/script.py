"""Script artifact — the production-aware output of Agent 2 (Ch. 8.5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .provenance import Provenance


class SceneCue(BaseModel):
    index: int
    narration: str  # exact words to be spoken (drives TTS)
    on_screen_text: str | None = None  # caption / lower-third / big-number callout
    b_roll_keywords: list[str] = Field(default_factory=list)
    fact_ref: int | None = None  # index into DataBrief.key_facts if this scene cites data
    sfx: str | None = None  # optional sound-effect keyword, mixed in at this scene's start


class Script(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["script"] = "script"
    template_id: str
    title_options: list[str] = Field(default_factory=list)
    hook: str
    scenes: list[SceneCue] = Field(default_factory=list)
    cta: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    thumbnail_concept: str = ""
    word_count: int = 0
    grounded_fact_refs: list[int] = Field(default_factory=list)
    synthetic_disclosure: bool = True
    provenance: Provenance
