"""VideoAsset artifact — the assembled, upload-ready mp4 (Ch. 12.5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .provenance import Provenance


class VideoAsset(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["video"] = "video"
    video_path: str  # assets/video.mp4
    duration_sec: float
    resolution: str  # e.g. "1920x1080"
    fps: int
    backend: str  # ffmpeg | moviepy | avatar
    has_captions: bool
    has_avatar: bool = False  # personal avatar overlay composited onto every frame
    file_size_bytes: int
    provenance: Provenance
