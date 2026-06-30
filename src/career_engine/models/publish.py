"""PublishResult artifact — the outcome of the YouTube upload (Ch. 13.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .provenance import Provenance


class PublishResult(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["publish"] = "publish"
    youtube_video_id: str | None = None
    video_url: str | None = None
    privacy_status: str  # private | unlisted | public
    disclosure_set: bool = False
    upload_status: str  # uploaded | failed | pending_manual_disclosure
    chosen_title: str
    published_at: datetime | None = None
    provenance: Provenance
