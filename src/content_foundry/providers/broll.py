"""Pexels stock B-roll client (Ch. 11.5). Disabled gracefully when no key is set."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BrollClient(Protocol):
    enabled: bool

    def search(self, query: str) -> list[str]:
        """Return candidate downloadable clip URLs for the query (best first; [] if no match)."""
        ...

    def download(self, url: str) -> bytes: ...


class NullBrollClient:
    """Used when no Pexels key is configured — every scene falls back to generation/card."""

    enabled = False

    def search(self, query: str) -> list[str]:
        return []

    def download(self, url: str) -> bytes:  # pragma: no cover - never called when disabled
        raise RuntimeError("B-roll is disabled")


class PexelsBrollClient:
    enabled = True
    _SEARCH_URL = "https://api.pexels.com/videos/search"

    def __init__(self, api_key: str, pool_size: int = 15) -> None:
        self._api_key = api_key
        self._pool_size = max(1, pool_size)

    def search(self, query: str) -> list[str]:
        import httpx

        resp = httpx.get(
            self._SEARCH_URL,
            headers={"Authorization": self._api_key},
            params={"query": query, "per_page": self._pool_size, "orientation": "landscape"},
            timeout=30,
        )
        resp.raise_for_status()
        urls: list[str] = []
        for video in resp.json().get("videos", []):
            files = sorted(video.get("video_files", []), key=lambda f: f.get("width", 0))
            if files:
                urls.append(files[-1]["link"])
        return urls

    def download(self, url: str) -> bytes:
        import httpx

        resp = httpx.get(url, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
