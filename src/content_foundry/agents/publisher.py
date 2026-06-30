"""Agent 7 — YouTube Publisher. Privacy-gated upload + enforced disclosure (Ch. 13)."""

from __future__ import annotations

from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, PublishResult, Script, VideoAsset, VisualPackage, utcnow
from ..production.seo import optimize_metadata
from ..safeguards.disclosure import ensure_description_discloses, resolve_publish_outcome


class Publisher:
    def __init__(self, settings, publisher):
        self._settings = settings
        self._pub = publisher
        self._log = get_logger(component="publisher")

    def run(
        self,
        run_id: str,
        video: VideoAsset,
        script: Script,
        visuals: VisualPackage,
        *,
        run_root: Path,
    ) -> PublishResult:
        s = self._settings
        if s.seo_optimize_enabled:
            meta = optimize_metadata(script, visuals, s)
            title, description, tags = meta.title, meta.description, meta.tags
        else:
            title = (script.title_options or ["Untitled career-advice video"])[0]
            description = script.description
            tags = script.tags
        description = ensure_description_discloses(description)
        video_real = str(run_root / video.video_path)
        thumb_real = str(run_root / visuals.thumbnail_path)

        # Always upload Private first; the disclosure gate decides the final privacy.
        video_id = self._pub.upload(
            video_path=video_real,
            title=title,
            description=description,
            tags=tags,
            category_id=s.youtube_category_id,
            privacy_status="private",
            default_language=s.youtube_default_language,
        )
        try:
            self._pub.set_thumbnail(video_id, thumb_real)
        except Exception as exc:  # a thumbnail failure must not abort the upload
            self._log.warning("thumbnail_failed", error=str(exc))

        disclosure_set = bool(self._pub.try_set_disclosure(video_id))
        effective_privacy, upload_status = resolve_publish_outcome(
            publish_mode=s.publish_mode,
            requested_privacy=s.youtube_privacy_status,
            disclosure_set=disclosure_set,
            require_manual_disclosure_before_public=s.require_manual_disclosure_before_public,
        )
        if effective_privacy != "private":
            self._pub.set_privacy(video_id, effective_privacy)

        return PublishResult(
            run_id=run_id,
            youtube_video_id=video_id,
            video_url=self._pub.video_url(video_id),
            privacy_status=effective_privacy,
            disclosure_set=disclosure_set,
            upload_status=upload_status,
            chosen_title=title,
            published_at=utcnow() if effective_privacy == "public" else None,
            provenance=Provenance(
                produced_by="publisher", model=None, config_hash=s.config_hash
            ),
        )
