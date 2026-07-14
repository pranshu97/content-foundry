"""Idea Miner — finds PROVEN video ideas from public YouTube data (Ch. 14.5).

Proof-of-concept over guessing: a video whose views tower over its OWN channel's median is strong
evidence the *idea* resonated, independent of channel size. The DEFAULT strategy is SEARCH-FIRST —
search videos for the run's topic (niche + idea) so candidates are on-topic by construction, then
keep the ones that outperformed their channel's median. When the operator pins channels instead, it
samples those channels' uploads and topic-ranks their outliers. Read-only Data API, no scraping.

Best-effort by design: any network / quota / parsing problem yields an empty list so a run is never
blocked — the pipeline simply falls back to the normal brainstormed ideas.
"""

from __future__ import annotations

import re
import statistics

from ..logging import get_logger
from ..models import MinedIdea
from ..providers.youtube_data import YouTubeDataClient

# Short tokens that ARE meaningful in tech/career niches (kept despite the >= 3-letter rule below).
_SHORT_KEEP = frozenset({"ai", "ml", "ar", "vr", "ux", "ui", "qa", "os", "db", "go", "js", "ts",
                         "cs", "hr", "pm", "ci", "cd"})

# Filler words stripped when building the relevance vocabulary, so a title must share a MEANINGFUL
# word with the run's niche/idea to count as on-topic (not "the"/"how"/"they"/a bare year).
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "how", "why", "what",
    "when", "where", "which", "who", "your", "you", "my", "our", "its", "their", "is", "are",
    "was", "were", "be", "been", "am", "do", "does", "did", "has", "have", "had", "can", "will",
    "would", "should", "get", "got", "best", "top", "this", "that", "these", "those", "they",
    "them", "it", "into", "each", "from", "out", "not", "but", "as", "at", "by", "so", "up",
    "about", "vs", "new", "guide", "tips", "tutorial", "video", "channel", "part", "full",
    "ever", "now", "here", "2023", "2024", "2025", "2026",
})


def _keywords(text: str) -> set[str]:
    """Meaningful lowercase words for topical matching: >= 3 letters (or a known short tech token like
    ``ai`` / ``ml``), minus stopwords."""
    return {
        w
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if (len(w) >= 3 or w in _SHORT_KEEP) and w not in _STOPWORDS
    }


def _on_topic(title: str, vocab: set[str]) -> bool:
    """A title is on-topic when it shares a meaningful word with the niche/idea vocab. An empty vocab
    (e.g. a one-word niche that reduces to nothing) treats everything as on-topic."""
    return not vocab or bool(_keywords(title) & vocab)


# A long-form channel gets no value mining Shorts (poor idea templates), and Shorts skew a channel's
# median which inflates multiples. Videos KNOWN to be shorter than this drop out (unknown = kept).
_MIN_LONGFORM_SEC = 120


def _is_short(video: dict) -> bool:
    duration = video.get("duration_sec", 0)
    return 0 < duration < _MIN_LONGFORM_SEC


class IdeaMiner:
    def __init__(self, settings, client: YouTubeDataClient) -> None:
        self._s = settings
        self._client = client
        self._log = get_logger(component="idea_miner")

    def mine(self, niche: str, *, focus: str = "") -> list[MinedIdea]:
        """Return up to ``idea_mining_max_ideas`` proven ideas relevant to ``niche`` (and the run's
        ``focus`` / --idea), strongest outlier first; [] if off, disabled, or anything goes wrong."""
        if not (self._s.idea_mining_enabled and getattr(self._client, "enabled", False)):
            return []
        try:
            return self._mine(niche, focus)
        except Exception as exc:  # network / quota / parse — never block the run
            self._log.warning("idea_mining_failed", error=str(exc))
            return []

    # ------------------------------------------------------------------ internals
    def _mine(self, niche: str, focus: str) -> list[MinedIdea]:
        # Pinned channels => sample THOSE (search can't target named channels). Otherwise search
        # VIDEOS by topic so candidates are on-topic by construction, then keep the channel-outliers.
        if self._s.idea_mining_channels_list:
            return self._mine_by_channels(niche, focus)
        return self._mine_by_search(niche, focus)

    def _mine_by_search(self, niche: str, focus: str) -> list[MinedIdea]:
        """DEFAULT: search videos for ``niche + idea`` (YouTube's own relevance ranking locks the
        topic), then keep the candidates that beat their OWN channel's median views (the proof)."""
        s = self._s
        query = " ".join(part for part in (niche, focus) if part).strip()
        video_ids = self._client.search_video_ids(query, limit=s.idea_mining_search_results)
        candidates = [
            v
            for v in self._client.video_stats(video_ids)
            if v.get("views", 0) > 0 and v.get("title")
            and v.get("live", "none") == "none" and not _is_short(v)
        ]
        medians: dict[str, float] = {}  # channel_id -> median views, computed once per channel
        found: list[MinedIdea] = []
        for v in candidates:
            channel_id = v.get("channel_id") or ""
            if not channel_id:
                continue
            if channel_id not in medians:
                try:
                    medians[channel_id] = self._channel_median(channel_id)
                except Exception as exc:  # a channel we can't baseline just can't contribute
                    self._log.warning("idea_mining_channel_skipped", channel=channel_id, error=str(exc))
                    medians[channel_id] = 0.0
            median = medians[channel_id]
            if median <= 0:
                continue
            multiple = v["views"] / median
            if multiple >= s.idea_mining_outlier_multiple:  # beat its channel's own average -> proven
                found.append(
                    MinedIdea(
                        title=v["title"].strip(),
                        channel_title=v.get("channel_title", ""),
                        views=int(v["views"]),
                        multiple=round(multiple, 1),
                        video_url=f"https://youtu.be/{v['id']}" if v.get("id") else "",
                    )
                )
        found.sort(key=lambda m: m.multiple, reverse=True)
        unique = self._dedup(found)
        if unique:
            self._log.info("mined_proven_ideas", count=len(unique), strategy="search")
        return unique[: s.idea_mining_max_ideas]

    def _mine_by_channels(self, niche: str, focus: str) -> list[MinedIdea]:
        """Operator pinned specific channels: sample THEIR uploads, keep each channel's outliers, and
        rank on-topic (niche/idea) first — search can't target named channels, so relevance is a soft
        boost here rather than the hard topic-lock the search path gets."""
        s = self._s
        vocab = _keywords(focus) | _keywords(niche)
        channels = self._client.resolve_channel_ids(s.idea_mining_channels_list)
        outliers: list[MinedIdea] = []
        for channel_id in channels[: s.idea_mining_max_channels]:
            try:
                outliers.extend(self._channel_outliers(channel_id))
            except Exception as exc:  # one bad channel (404 uploads, quota) must not sink the rest
                self._log.warning("idea_mining_channel_skipped", channel=channel_id, error=str(exc))
        outliers.sort(key=lambda m: (_on_topic(m.title, vocab), m.multiple), reverse=True)
        unique = self._dedup(outliers)
        if unique:
            self._log.info("mined_proven_ideas", count=len(unique), strategy="channels")
        return unique[: s.idea_mining_max_ideas]

    def _channel_median(self, channel_id: str) -> float:
        """A channel's 'normal' bar = median views of its recent uploads (0.0 if too small a sample)."""
        uploads = self._client.uploads_playlist_id(channel_id)
        if not uploads:
            return 0.0
        video_ids = self._client.recent_video_ids(
            uploads, limit=self._s.idea_mining_videos_per_channel
        )
        views = [
            v["views"]
            for v in self._client.video_stats(video_ids)
            if v.get("views", 0) > 0 and v.get("live", "none") == "none" and not _is_short(v)
        ]
        if len(views) < 5:
            return 0.0
        return statistics.median(views)

    def _channel_outliers(self, channel_id: str) -> list[MinedIdea]:
        uploads = self._client.uploads_playlist_id(channel_id)
        if not uploads:
            return []
        video_ids = self._client.recent_video_ids(
            uploads, limit=self._s.idea_mining_videos_per_channel
        )
        stats = [
            v
            for v in self._client.video_stats(video_ids)
            if v.get("views", 0) > 0 and v.get("title")
            and v.get("live", "none") == "none" and not _is_short(v)
        ]
        if len(stats) < 5:  # too small a sample to call anything an outlier fairly
            return []
        median = statistics.median(v["views"] for v in stats)  # the channel's OWN normal bar
        if median <= 0:
            return []
        threshold = self._s.idea_mining_outlier_multiple
        found: list[MinedIdea] = []
        for v in stats:
            multiple = v["views"] / median
            if multiple >= threshold:  # beats the channel's own median by a wide margin
                found.append(
                    MinedIdea(
                        title=v["title"].strip(),
                        channel_title=v.get("channel_title", ""),
                        views=int(v["views"]),
                        multiple=round(multiple, 1),
                        video_url=f"https://youtu.be/{v['id']}" if v.get("id") else "",
                    )
                )
        return found

    @staticmethod
    def _dedup(ideas: list[MinedIdea]) -> list[MinedIdea]:
        """Drop near-identical titles (case-insensitive), keeping the first — i.e. strongest — one."""
        seen: set[str] = set()
        unique: list[MinedIdea] = []
        for idea in ideas:
            key = idea.title.strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(idea)
        return unique
