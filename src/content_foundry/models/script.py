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
    editor_note: str | None = None  # short editing/direction note for the editor (NEVER spoken)
    cut: str | None = None  # pacing hint (fast|quick|hard|hold|slow...) — steers shot density


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
    thumbnail_text: str = ""  # dedicated punchy overlay TEXT for the thumbnail (may differ from title)
    word_count: int = 0
    grounded_fact_refs: list[int] = Field(default_factory=list)
    synthetic_disclosure: bool = True
    time_sensitive: bool = False  # LLM's call: is the topic time-bound? (drives year-stamping)
    provenance: Provenance
