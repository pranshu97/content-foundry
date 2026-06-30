"""Pexels stock B-roll client (Ch. 11.5). Disabled gracefully when no key is set."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BrollClient(Protocol):
    enabled: bool

    def search(self, query: str) -> str | None:
        """Return a downloadable clip URL for the query, or None if no match."""
        ...

    def download(self, url: str) -> bytes: ...


class NullBrollClient:
    """Used when no Pexels key is configured — every scene falls back to generation/card."""

    enabled = False

    def search(self, query: str) -> str | None:
        return None

    def download(self, url: str) -> bytes:  # pragma: no cover - never called when disabled
        raise RuntimeError("B-roll is disabled")


class PexelsBrollClient:
    enabled = True
    _SEARCH_URL = "https://api.pexels.com/videos/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def search(self, query: str) -> str | None:
        import httpx

        resp = httpx.get(
            self._SEARCH_URL,
            headers={"Authorization": self._api_key},
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            timeout=30,
        )
        resp.raise_for_status()
        videos = resp.json().get("videos", [])
        if not videos:
            return None
        files = videos[0].get("video_files", [])
        files.sort(key=lambda f: f.get("width", 0))
        return files[-1]["link"] if files else None

    def download(self, url: str) -> bytes:
        import httpx

        resp = httpx.get(url, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
