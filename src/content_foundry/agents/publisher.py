"""Agent 7 — YouTube Publisher. Privacy-gated upload + enforced disclosure (Ch. 13)."""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, PublishResult, Script, VideoAsset, VisualPackage, utcnow
from ..production.affiliate import AffiliateLink, affiliate_block
from ..production.seo import channel_cta_block, optimize_metadata, youtube_safe_text
from ..safeguards.disclosure import resolve_publish_outcome

# A just-uploaded video is often still processing, so YouTube rejects thumbnails.set for a few seconds
# (the thumbnail then silently never appears). Retry with backoff before giving up — best-effort, a
# thumbnail failure must never abort the upload.
_THUMB_SET_ATTEMPTS = 4
_THUMB_SET_BACKOFF_SEC = 5.0

_VIDEO_ID_RE = re.compile(r"(?:youtu\.be/|[?&]v=|/embed/|/shorts/)([A-Za-z0-9_-]{6,})")


def _video_id_from_link(link: str) -> str:
    """The video id inside a watch/share link, or "" when the link isn't a YouTube video URL."""
    match = _VIDEO_ID_RE.search(link or "")
    return match.group(1) if match else ""


class Publisher:
    def __init__(self, settings, publisher, search_provider=None):
        self._settings = settings
        self._pub = publisher
        self._search = (
            search_provider  # kept for compatibility; affiliate links now resolve pre-gen
        )
        self._log = get_logger(component="publisher")

    def _affiliate_links(self, script: Script) -> list[AffiliateLink]:
        """The resource links the SCRIPT already committed to (resolved BEFORE generation + name-
        scanned, persisted on the script). Rebuilt from ``script.affiliate_links`` — never re-searched
        here, so the description matches EXACTLY what the narration promised."""
        if not getattr(self._settings, "affiliate_enabled", False):
            return []
        return [AffiliateLink(**d) for d in (getattr(script, "affiliate_links", None) or [])]

    def _recommendations_comment(
        self, run_id: str, title: str, script: Script, run_root: Path
    ) -> str:
        """A 'watch next' comment block linking the most related PRIOR videos on the channel (the same
        picks as the end screen) so a fresh upload pulls viewers deeper. Empty when there are no prior
        published videos or on any failure — nothing spurious is ever posted. Works for both formats."""
        try:
            from ..production.end_screen import build_end_screen, recommendations_comment

            payload = build_end_screen(
                run_root.parent,
                run_id=run_id,
                title=title,
                tags=list(getattr(script, "tags", []) or []),
                niche=self._settings.target_niche,
                count=self._settings.end_screen_count,
            )
            recs = self._with_live_titles(payload.get("recommendations", []))
            return recommendations_comment(
                recs,
                header=self._settings.recommend_comment_header,
            )
        except Exception as exc:  # a recommendations failure must never abort the upload
            self._log.warning("recommend_comment_failed", error=str(exc))
            return ""

    def _with_live_titles(self, recs: list[dict]) -> list[dict]:
        """Replace each recommendation's name with the title the video CURRENTLY has on YouTube.

        The picks come from local run history, which records the title as it was at upload time — so
        a video renamed in Studio afterwards would be linked under a name that no longer exists.
        Best-effort and read-only (the public Data-API key): with no key, no network, or no match, the
        stored name is kept.
        """
        ids = [_video_id_from_link(str(r.get("link") or "")) for r in recs]
        ids = [i for i in ids if i]
        if not ids:
            return recs
        try:
            from ..providers import build_youtube_data_client

            live = {
                v.get("id"): (v.get("title") or "").strip()
                for v in build_youtube_data_client(self._settings).video_stats(ids)
            }
        except Exception as exc:  # never block a publish on a title refresh
            self._log.warning("recommend_title_refresh_failed", error=str(exc))
            return recs
        out: list[dict] = []
        for rec in recs:
            vid = _video_id_from_link(str(rec.get("link") or ""))
            title = live.get(vid, "")
            if title and title != rec.get("name"):
                self._log.info("recommend_title_refreshed", video_id=vid, title=title)
                rec = {**rec, "name": title}
            out.append(rec)
        return out

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
        # Affiliate resources the SCRIPT committed to (resolved pre-generation, name-scanned, stored on
        # the script) + the disclosure. Placed ABOVE the chapters (more visible). A no-op when off.
        aff_links = self._affiliate_links(script)
        aff_block = affiliate_block(aff_links, s)
        if s.seo_optimize_enabled:
            meta = optimize_metadata(script, visuals, s, affiliate_block=aff_block)
            title, description, tags = meta.title, meta.description, meta.tags
        else:
            title = (script.title_options or ["Untitled career-advice video"])[0]
            description = script.description
            if aff_block:
                description = f"{description}\n\n{aff_block}"
            tags = script.tags
        # YouTube's API rejects a title/description containing '<' or '>' (400 invalidDescription) — the
        # auto-built chapters can inherit one from a scene's on_screen_text (e.g. "PROCESS > RESULT").
        title = youtube_safe_text(title)
        description = youtube_safe_text(description)
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
        # YouTube SHORTS take their thumbnail from a video FRAME — a custom thumbnails.set is ignored
        # (custom Short thumbnails are a limited, mobile-only rollout, not in the Data API). Skip the
        # futile call for a Short unless the operator's account has the rollout; long-form always sets.
        if s.is_short and not s.publish_shorts_custom_thumbnail:
            self._log.info(
                "shorts_thumbnail_skipped",
                hint="YouTube uses a video frame for a Short's thumbnail; the uploaded thumbnail.png "
                "is not applied via the API. Set PUBLISH_SHORTS_CUSTOM_THUMBNAIL=true only if your "
                "account supports custom Short thumbnails.",
            )
        else:
            # A manually-swapped thumbnail.png can exceed YouTube's 2 MB custom-thumbnail limit (the
            # pipeline caps its OWN render, but a hand-replaced high-res image bypasses that), so
            # thumbnails.set 400s and the video ends up with NO custom thumbnail. Re-optimize in place
            # first so a personal thumbnail uploads too. Best-effort.
            from .visuals import ensure_upload_safe_thumbnail

            if ensure_upload_safe_thumbnail(Path(thumb_real)):
                self._log.info("thumbnail_optimized_for_upload", path=thumb_real)
            # Buffer: let YouTube finish processing the just-uploaded video before setting the
            # thumbnail (thumbnails.set is rejected while the video is still processing), then retry.
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

        disclosure_set = bool(self._pub.try_set_disclosure(video_id))
        effective_privacy, upload_status = resolve_publish_outcome(
            publish_mode=s.publish_mode,
            requested_privacy=s.youtube_privacy_status,
            disclosure_set=disclosure_set,
            require_manual_disclosure_before_public=s.require_manual_disclosure_before_public,
        )
        if effective_privacy != "private":
            self._pub.set_privacy(video_id, effective_privacy)

        # Best-effort: post ONE top comment (identical for Shorts and long-form; needs the force-ssl
        # scope). It links the most related PRIOR videos on the channel — the same "watch next" picks
        # as the end screen — and optionally adds an affiliate/subscribe nudge. Posted even when
        # PUBLISH_TOP_COMMENT is off, as long as there are videos to recommend.
        #
        # ORDER MATTERS: this runs AFTER set_privacy. Every upload starts PRIVATE (the disclosure
        # gate), and YouTube refuses comments on a private video — posting here used to fail on every
        # single publish and the best-effort handler swallowed it, so the comment silently never
        # appeared. A video that stays private still can't take one, which is why that is logged.
        comment = ""
        if hasattr(self._pub, "add_comment"):
            parts: list[str] = []
            if s.recommend_comment_enabled:
                rec_block = self._recommendations_comment(run_id, title, script, run_root)
                if rec_block:
                    parts.append(rec_block)
            if s.publish_top_comment:
                if s.affiliate_in_comment and aff_block:
                    parts.append(aff_block)
                parts.append((channel_cta_block(s) or s.channel_cta_text or "").strip())
            comment = youtube_safe_text("\n\n".join(p for p in parts if p).strip())
            if comment and effective_privacy == "private":
                self._log.warning(
                    "comment_skipped_private_video",
                    hint="YouTube refuses comments on a private video; set YOUTUBE_PRIVACY_STATUS "
                    "to unlisted/public (or publish it) and the comment will post",
                )
            elif comment:
                try:
                    self._pub.add_comment(video_id, comment)
                    self._log.info("comment_posted", chars=len(comment))
                except Exception as exc:  # a comment failure must not abort the upload
                    self._log.warning("comment_failed", error=str(exc)[:300])

        return PublishResult(
            run_id=run_id,
            youtube_video_id=video_id,
            video_url=self._pub.video_url(video_id),
            privacy_status=effective_privacy,
            disclosure_set=disclosure_set,
            upload_status=upload_status,
            chosen_title=title,
            description=description,
            pinned_comment=comment,
            affiliate_links=[
                {"label": lnk.label, "url": lnk.url, "blurb": lnk.blurb} for lnk in aff_links
            ],
            published_at=utcnow() if effective_privacy == "public" else None,
            provenance=Provenance(produced_by="publisher", model=None, config_hash=s.config_hash),
        )
