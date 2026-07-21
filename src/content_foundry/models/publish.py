"""PublishResult artifact — the outcome of the YouTube upload (Ch. 13.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    # The FINAL text actually sent to YouTube + the affiliate links embedded in it, persisted here so
    # what was published is auditable (the Amazon link is a LIVE search at publish time, not stored
    # anywhere else — re-publishing may re-resolve it).
    description: str = ""
    pinned_comment: str = ""
    affiliate_links: list[dict] = Field(default_factory=list)  # [{label, url, blurb}]
    published_at: datetime | None = None
    provenance: Provenance
