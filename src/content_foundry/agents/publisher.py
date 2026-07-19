"""Agent 7 — YouTube Publisher. Privacy-gated upload + enforced disclosure (Ch. 13)."""

from __future__ import annotations

import time
from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, PublishResult, Script, VideoAsset, VisualPackage, utcnow
from ..production.affiliate import (
    AffiliateLink,
    affiliate_block,
    amazon_search_query,
    resolve_links,
    tag_amazon_url,
)
from ..production.seo import channel_cta_block, optimize_metadata
from ..safeguards.disclosure import resolve_publish_outcome

# A just-uploaded video is often still processing, so YouTube rejects thumbnails.set for a few seconds
# (the thumbnail then silently never appears). Retry with backoff before giving up — best-effort, a
# thumbnail failure must never abort the upload.
_THUMB_SET_ATTEMPTS = 4
_THUMB_SET_BACKOFF_SEC = 5.0


class Publisher:
    def __init__(self, settings, publisher, search_provider=None):
        self._settings = settings
        self._pub = publisher
        self._search = search_provider
        self._log = get_logger(component="publisher")

    def _affiliate_links(self, script: Script, tags: list[str]) -> list[AffiliateLink]:
        """Topic-relevant affiliate links for this video (referrals + an optional real Amazon product).
        Empty when AFFILIATE_ENABLED is off."""
        s = self._settings
        if not getattr(s, "affiliate_enabled", False):
            return []
        script_text = " ".join((sc.narration or "") for sc in script.scenes)
        # The Amazon BOOK query uses the script's own topical tags (more specific than the niche-seeded
        # SEO tags) so the found book is canonical to the topic.
        amazon = self._amazon_link(list(script.tags) or list(tags))
        return resolve_links(s, tags=tags, script_text=script_text, amazon_link=amazon)

    def _amazon_link(self, tags: list[str]) -> AffiliateLink | None:
        """Best-effort: find a REAL Amazon product URL via the search provider and append the associate
        tag. ``None`` when disabled, no search provider, or nothing valid is found — never a guessed
        URL."""
        s = self._settings
        if not (getattr(s, "amazon_assoc_tag", "") or "").strip() or self._search is None:
            return None
        try:
            query = amazon_search_query(tags, s.target_niche)
            results = self._search.search(f"{query} site:amazon.com", 5) or []
            for r in results:
                tagged = tag_amazon_url(getattr(r, "url", ""), s.amazon_assoc_tag)
                if tagged:
                    return AffiliateLink("Recommended book (Amazon)", tagged)
        except Exception as exc:  # a flaky search must never break publishing
            self._log.warning("affiliate_amazon_failed", error=str(exc))
        return None

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
        video_real = str(run_root / video.video_path)
        thumb_real = str(run_root / visuals.thumbnail_path)

        # Affiliate resources (optional monetization). Topic-relevant referral links + a real Amazon
        # product (found via search, never invented) + a required disclosure, appended to EVERY
        # description. A no-op when AFFILIATE_ENABLED is off.
        aff_links = self._affiliate_links(script, list(tags))
        aff_block = affiliate_block(aff_links, s)
        if aff_block:
            description = f"{description}\n\n{aff_block}"

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
        # Buffer: let YouTube finish processing the just-uploaded video before setting the thumbnail
        # (thumbnails.set is rejected while the video is still processing), then retry with backoff.
        if s.publish_thumbnail_delay_sec > 0:
            self._log.info("thumbnail_buffer", seconds=s.publish_thumbnail_delay_sec)
            time.sleep(s.publish_thumbnail_delay_sec)
        for attempt in range(1, _THUMB_SET_ATTEMPTS + 1):
            try:
                self._pub.set_thumbnail(video_id, thumb_real)
                break
            except Exception as exc:  # a thumbnail failure must not abort the upload
                if attempt >= _THUMB_SET_ATTEMPTS:
                    self._log.warning("thumbnail_failed", error=str(exc), attempts=attempt)
                else:
                    self._log.info("thumbnail_retry", attempt=attempt, error=str(exc)[:200])
                    time.sleep(_THUMB_SET_BACKOFF_SEC * attempt)

        # Best-effort: file the upload into a series playlist (session watch time). Only when it's
        # configured AND the publisher supports it (the null/fake publishers don't) — never fatal.
        playlist_id = (s.youtube_playlist_id or "").strip()
        if playlist_id and hasattr(self._pub, "add_to_playlist"):
            try:
                self._pub.add_to_playlist(video_id, playlist_id)
            except Exception as exc:  # a playlist-add failure must not abort the upload
                self._log.warning("playlist_add_failed", error=str(exc))

        # Best-effort: post a top comment nudging viewers to subscribe/explore (a soft channel pin;
        # opt-in via PUBLISH_TOP_COMMENT, needs the force-ssl scope). Never fatal to the upload.
        if s.publish_top_comment and hasattr(self._pub, "add_comment"):
            parts = [aff_block] if (s.affiliate_in_comment and aff_block) else []
            parts.append((channel_cta_block(s) or s.channel_cta_text or "").strip())
            comment = "\n\n".join(p for p in parts if p).strip()
            if comment:
                try:
                    self._pub.add_comment(video_id, comment)
                except Exception as exc:  # a comment failure must not abort the upload
                    self._log.warning("comment_failed", error=str(exc))

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
