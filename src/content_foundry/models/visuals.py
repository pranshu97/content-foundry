"""VisualPackage artifact — thumbnail, per-scene visuals, captions (Ch. 11.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .provenance import Provenance


class VisualShot(BaseModel):
    """One B-roll clip covering a single beat within a scene (finer-grained than one clip/scene)."""

    path: str  # assets/scenes/scene_<n>_shot_<k>.mp4
    duration_sec: float
    source: str  # pexels | pixabay | stock
    query: str  # the shot description used to find it


class SceneVisual(BaseModel):
    scene_index: int
    kind: Literal["image", "broll"]
    path: str  # assets/scenes/scene_<n>.{png|mp4}
    source: str  # openai | stability | pexels | card
    prompt_or_query: str
    on_screen_text: str | None = None  # caption / source citation to burn onto the frame
    sfx: str | None = None  # sound-effect keyword to mix at this scene's start
    duration_sec: float  # mirrors scene timing
    shots: list[VisualShot] = Field(default_factory=list)  # ordered beats (sub-clips) within scene


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
