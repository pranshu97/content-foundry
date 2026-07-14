"""Read-only YouTube Data API v3 client (API-key auth — no OAuth, no scraping) used to mine PROVEN
video ideas: real outlier videos that far outperformed their own channel's typical views (Ch. 14.5).

Only public read endpoints are touched (``search``/``channels``/``playlistItems``/``videos``), so a
plain Data-API key is enough — no user consent, no write scope. A blank ``YOUTUBE_API_KEY`` yields a
disabled :class:`NullYouTubeDataClient`, keeping the feature entirely opt-in and the repo generic.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

_BASE = "https://www.googleapis.com/youtube/v3"
_ISO_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _iso_seconds(duration: str) -> int:
    """Seconds from an ISO-8601 video duration like ``PT5M30S``; 0 when absent/unparseable."""
    match = _ISO_DURATION.fullmatch(duration or "")
    if not match:
        return 0
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


@runtime_checkable
class YouTubeDataClient(Protocol):
    """Minimal read surface the idea miner needs. ``enabled`` is False for the null client."""

    enabled: bool

    def search_channel_ids(self, query: str, *, limit: int) -> list[str]: ...
    def search_video_ids(self, query: str, *, limit: int) -> list[str]: ...
    def resolve_channel_ids(self, handles: list[str]) -> list[str]: ...
    def uploads_playlist_id(self, channel_id: str) -> str | None: ...
    def recent_video_ids(self, playlist_id: str, *, limit: int) -> list[str]: ...
    def video_stats(self, video_ids: list[str]) -> list[dict]: ...


class NullYouTubeDataClient:
    """No-op client returned when no API key is configured; the miner then proposes nothing."""

    enabled = False

    def search_channel_ids(self, query: str, *, limit: int) -> list[str]:
        return []

    def search_video_ids(self, query: str, *, limit: int) -> list[str]:
        return []

    def resolve_channel_ids(self, handles: list[str]) -> list[str]:
        return []

    def uploads_playlist_id(self, channel_id: str) -> str | None:
        return None

    def recent_video_ids(self, playlist_id: str, *, limit: int) -> list[str]:
        return []

    def video_stats(self, video_ids: list[str]) -> list[dict]:
        return []


class ApiYouTubeDataClient:
    """Thin API-key client over the public Data API v3 read endpoints. Methods return plain lists so
    the miner stays vendor-agnostic; the miner (not this client) decides what counts as an outlier."""

    enabled = True

    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        self._key = api_key
        self._timeout = timeout

    def _get(self, path: str, params: dict) -> dict:
        import httpx

        resp = httpx.get(f"{_BASE}/{path}", params={**params, "key": self._key}, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def search_channel_ids(self, query: str, *, limit: int) -> list[str]:
        """Channel ids for niche channels most relevant to ``query`` (search.list, 100 quota units)."""
        data = self._get(
            "search",
            {"part": "snippet", "q": query, "type": "channel", "maxResults": min(50, max(1, limit))},
        )
        ids: list[str] = []
        for item in data.get("items", []):
            cid = (item.get("id") or {}).get("channelId") or item.get("snippet", {}).get("channelId")
            if cid and cid not in ids:
                ids.append(cid)
        return ids[:limit]

    def search_video_ids(self, query: str, *, limit: int) -> list[str]:
        """Video ids most RELEVANT to ``query`` (search.list type=video, 100 quota units). YouTube's own
        relevance ranking does the topical matching, so the candidates are on-topic by construction."""
        data = self._get(
            "search",
            {"part": "snippet", "q": query, "type": "video", "order": "relevance",
             "maxResults": min(50, max(1, limit))},
        )
        ids: list[str] = []
        for item in data.get("items", []):
            vid = (item.get("id") or {}).get("videoId")
            if vid and vid not in ids:
                ids.append(vid)
        return ids[:limit]

    def resolve_channel_ids(self, handles: list[str]) -> list[str]:
        """Turn operator-supplied @handles / channel URLs / ids into canonical ``UC…`` channel ids."""
        out: list[str] = []
        for raw in handles:
            token = (raw or "").strip()
            if not token:
                continue
            if token.startswith("UC") and len(token) >= 20:  # already a channel id
                out.append(token)
                continue
            handle = token.lstrip("@").rstrip("/").rsplit("/", 1)[-1]  # accept @name or a URL tail
            items = self._get("channels", {"part": "id", "forHandle": handle}).get("items", [])
            if not items:  # legacy channels predate @handles — fall back to the custom username
                items = self._get("channels", {"part": "id", "forUsername": handle}).get("items", [])
            if items and items[0].get("id"):
                out.append(items[0]["id"])
        return out

    def uploads_playlist_id(self, channel_id: str) -> str | None:
        """The special 'uploads' playlist holding every public upload of a channel (channels.list)."""
        items = self._get("channels", {"part": "contentDetails", "id": channel_id}).get("items", [])
        if not items:
            return None
        related = items[0].get("contentDetails", {}).get("relatedPlaylists", {})
        return related.get("uploads")

    def recent_video_ids(self, playlist_id: str, *, limit: int) -> list[str]:
        """Most-recent video ids from an uploads playlist, paging 50 at a time up to ``limit``."""
        out: list[str] = []
        page: str | None = None
        while len(out) < limit:
            params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
            if page:
                params["pageToken"] = page
            data = self._get("playlistItems", params)
            for item in data.get("items", []):
                vid = item.get("contentDetails", {}).get("videoId")
                if vid:
                    out.append(vid)
            page = data.get("nextPageToken")
            if not page:
                break
        return out[:limit]

    def video_stats(self, video_ids: list[str]) -> list[dict]:
        """Per-video title / channel / publish date / view count (videos.list, 50 ids per call)."""
        out: list[dict] = []
        for start in range(0, len(video_ids), 50):
            chunk = video_ids[start : start + 50]
            if not chunk:
                continue
            data = self._get(
                "videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk)}
            )
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                try:
                    views = int(stats.get("viewCount", 0) or 0)
                except (TypeError, ValueError):
                    views = 0
                out.append(
                    {
                        "id": item.get("id", ""),
                        "title": snippet.get("title", ""),
                        "channel_title": snippet.get("channelTitle", ""),
                        "channel_id": snippet.get("channelId", ""),
                        "published_at": snippet.get("publishedAt", ""),
                        "views": views,
                        "live": snippet.get("liveBroadcastContent", "none"),
                        "duration_sec": _iso_seconds(item.get("contentDetails", {}).get("duration", "")),
                    }
                )
        return out
