"""Stock B-roll clients: Pexels + Pixabay, aggregated by MultiBrollClient (Ch. 11.5).

Disabled gracefully (NullBrollClient) when no key is set; each scene then falls back to generation.
"""

from __future__ import annotations

from itertools import zip_longest
from typing import Protocol, runtime_checkable


def _download_bytes(url: str) -> bytes:
    import httpx

    resp = httpx.get(url, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _interleave(pools: list[list[str]]) -> list[str]:
    """Round-robin merge several result pools (de-duplicated), so no single source dominates and
    each scene draws from a varied mix."""
    out: list[str] = []
    seen: set[str] = set()
    for row in zip_longest(*pools):
        for url in row:
            if url and url not in seen:
                seen.add(url)
                out.append(url)
    return out


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
    name = "pexels"
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
        return _download_bytes(url)


class PixabayBrollClient:
    """Free stock video from Pixabay (needs a free API key). A second source so scenes draw from a
    bigger pool and different videos end up looking different."""

    enabled = True
    name = "pixabay"
    _SEARCH_URL = "https://pixabay.com/api/videos/"

    def __init__(self, api_key: str, pool_size: int = 15) -> None:
        self._api_key = api_key
        self._pool_size = min(200, max(3, pool_size))  # Pixabay requires per_page in [3, 200]

    def search(self, query: str) -> list[str]:
        import httpx

        resp = httpx.get(
            self._SEARCH_URL,
            params={"key": self._api_key, "q": query, "per_page": self._pool_size},
            timeout=30,
        )
        resp.raise_for_status()
        urls: list[str] = []
        for hit in resp.json().get("hits", []):
            renditions = hit.get("videos", {})
            for size in ("large", "medium", "small", "tiny"):
                link = (renditions.get(size) or {}).get("url")
                if link:
                    urls.append(link)
                    break
        return urls

    def download(self, url: str) -> bytes:
        return _download_bytes(url)


class MultiBrollClient:
    """Aggregate several B-roll clients into one bigger, varied pool. Resilient: if one source
    errors (e.g. rate-limited), the others still contribute."""

    def __init__(self, clients: list[BrollClient]) -> None:
        self._clients = [c for c in clients if getattr(c, "enabled", False)]

    @property
    def enabled(self) -> bool:
        return bool(self._clients)

    def search(self, query: str) -> list[str]:
        pools: list[list[str]] = []
        for client in self._clients:
            try:
                pools.append(client.search(query))
            except Exception:  # one source failing must not sink the scene
                pools.append([])
        return _interleave(pools)

    def download(self, url: str) -> bytes:
        return _download_bytes(url)
