"""Idea Miner — finds PROVEN video ideas from public YouTube data (Ch. 14.5).

The strategy is proof-of-concept over guessing: within a niche, a video whose view count towers over
its OWN channel's median is strong evidence the *idea* resonated, controlling for how large or small
the channel is. The miner samples a few niche channels via the read-only Data API (no scraping),
computes each channel's median views, and returns the videos that beat that median by a wide margin.

Best-effort by design: any network / quota / parsing problem yields an empty list so a run is never
blocked — the pipeline simply falls back to the normal brainstormed ideas.
"""

from __future__ import annotations

import re
import statistics

from ..logging import get_logger
from ..models import MinedIdea
from ..providers.youtube_data import YouTubeDataClient

# Filler words stripped when building the relevance vocabulary, so a title must share a MEANINGFUL
# word with the run's niche/idea (not just "the"/"how"/"best") to count as on-topic.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "how", "why", "what",
    "when", "your", "you", "my", "is", "are", "be", "get", "best", "top", "this", "that", "from",
    "vs", "new", "guide", "tips", "tutorial", "video", "channel", "part", "full", "ever",
    "2023", "2024", "2025", "2026",
})


def _keywords(text: str) -> set[str]:
    """Meaningful lowercase words (>= 3 letters, minus stopwords) used for topical matching."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in _STOPWORDS}


def _on_topic(title: str, vocab: set[str]) -> bool:
    """A title is on-topic when it shares a meaningful word with the niche/idea vocab. An empty vocab
    (e.g. a one-word niche that reduces to nothing) disables the gate rather than dropping every idea."""
    return not vocab or bool(_keywords(title) & vocab)


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
        s = self._s
        # Relevance vocab = the run's IDEA when given, else the niche. Every mined idea is gated on it
        # so a channel's off-topic viral hit never leaks in; the median baseline still uses ALL uploads.
        vocab = _keywords(focus) or _keywords(niche)
        channels = self._channel_ids(niche, focus)[: s.idea_mining_max_channels]
        outliers: list[MinedIdea] = []
        for channel_id in channels:
            outliers.extend(self._channel_outliers(channel_id, vocab))
        outliers.sort(key=lambda m: m.multiple, reverse=True)
        unique = self._dedup(outliers)
        if unique:
            self._log.info("mined_proven_ideas", count=len(unique), channels=len(channels))
        return unique[: s.idea_mining_max_ideas]

    def _channel_ids(self, niche: str, focus: str) -> list[str]:
        """Operator-pinned channels win; otherwise DYNAMICALLY search YouTube for channels matching the
        niche (+ the run's idea) — never a hardcoded list, so it adapts to whatever you ask for."""
        pinned = self._s.idea_mining_channels_list
        if pinned:
            return self._client.resolve_channel_ids(pinned)
        query = " ".join(part for part in (niche, focus) if part).strip()
        return self._client.search_channel_ids(query, limit=self._s.idea_mining_max_channels)

    def _channel_outliers(self, channel_id: str, vocab: set[str]) -> list[MinedIdea]:
        uploads = self._client.uploads_playlist_id(channel_id)
        if not uploads:
            return []
        video_ids = self._client.recent_video_ids(
            uploads, limit=self._s.idea_mining_videos_per_channel
        )
        stats = [
            v
            for v in self._client.video_stats(video_ids)
            if v.get("views", 0) > 0 and v.get("title") and v.get("live", "none") == "none"
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
            # Proven idea = beats its channel's median AND is on the run's niche/idea (topical gate),
            # so the picker only ever shows outliers relevant to what you actually asked for.
            if multiple >= threshold and _on_topic(v["title"], vocab):
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
