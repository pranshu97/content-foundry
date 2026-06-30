"""VisualPackage artifact — thumbnail, per-scene visuals, captions (Ch. 11.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .provenance import Provenance


class SceneVisual(BaseModel):
    scene_index: int
    kind: Literal["image", "broll"]
    path: str  # assets/scenes/scene_<n>.{png|mp4}
    source: str  # openai | stability | pexels | card
    prompt_or_query: str
    duration_sec: float  # mirrors scene timing


class VisualPackage(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["visuals"] = "visuals"
    thumbnail_path: str  # assets/thumbnail.png
    thumbnail_text: str
    captions_path: str  # assets/captions.srt
    scenes: list[SceneVisual] = Field(default_factory=list)
    visual_style: str
    provenance: Provenance
