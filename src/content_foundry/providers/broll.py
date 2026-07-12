"""Stock B-roll clients: Pexels + Pixabay + Coverr, aggregated by MultiBrollClient (Ch. 11.5).

Disabled gracefully (NullBrollClient) when no key is set; each scene then falls back to generation.
"""

from __future__ import annotations

import random
import re
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


# Front-biased page picker: repeated searches for the same keyword pull DIFFERENT clips (much more
# variety across videos) while still usually hitting the most-relevant first page. A source that
# runs out of pages just errors and is skipped by MultiBrollClient / the visuals layer.
_PAGE_WEIGHTS = (6, 3, 1)  # ~60% page 1, ~30% page 2, ~10% page 3


def _pick_page(rng: random.Random, *, base: int = 1) -> int:
    """Return a front-biased page number. ``base`` is 1 for Pexels/Pixabay and 0 for Coverr (0-indexed)."""
    pages = list(range(base, base + len(_PAGE_WEIGHTS)))
    return rng.choices(pages, weights=list(_PAGE_WEIGHTS), k=1)[0]


# Subjects stock sites pad generic queries with even when they are unrelated to the video — a moon
# time-lapse for "busy office", a lipstick close-up for "person smiling". A clip is dropped when its
# OWN tags/slug name one of these AND the query never asked for it, so a clip whose subject the query
# really did request (an astronomy video that queries "moon") is still kept. Deliberately EXCLUDES
# tech-ambiguous words (cloud, star, tree, network, data) so genuine B-roll is never filtered.
_OFF_TOPIC_SUBJECTS = frozenset({
    # celestial / sky scenery
    "moon", "lunar", "galaxy", "galaxies", "planet", "planets", "nebula", "cosmos", "cosmic",
    "universe", "aurora", "eclipse", "meteor", "comet", "sunset", "sunrise", "twilight", "dusk",
    # beauty / cosmetics
    "lipstick", "makeup", "mascara", "eyeshadow", "eyeliner", "cosmetic", "cosmetics", "skincare",
    "manicure", "pedicure", "perfume", "salon", "spa", "lipgloss",
    # animals / wildlife
    "cat", "cats", "kitten", "dog", "dogs", "puppy", "pet", "pets", "wildlife", "bird", "birds",
    "horse", "cow", "sheep", "goat", "insect", "insects", "butterfly", "bee", "spider", "fish",
    "dolphin", "whale", "lion", "tiger", "elephant", "monkey", "deer", "rabbit",
    # nature / travel scenery
    "flower", "flowers", "floral", "blossom", "waterfall", "beach", "ocean", "sea", "seascape",
    "seaside", "mountain", "mountains", "jungle", "forest", "meadow", "sunflower", "tulip", "rose",
    "coral", "safari", "vineyard",
    # food / drink
    "pizza", "burger", "cake", "dessert", "cupcake", "cocktail", "smoothie", "sushi", "pancake",
    "wine", "beer", "champagne",
    # romance / celebration clichés
    "wedding", "bride", "groom", "kiss", "kissing", "romantic", "romance", "honeymoon", "fireworks",
    "confetti", "balloon", "balloons",
    # love / valentine / holidays / greetings (stock "greeting-card" padding)
    "valentine", "valentines", "love", "heart", "hearts", "dating", "couple", "couples",
    "christmas", "xmas", "santa", "halloween", "easter", "thanksgiving", "holiday", "holidays",
    "festive", "festival", "birthday", "party", "celebration", "celebrate", "anniversary",
    "gift", "gifts", "present", "greeting", "greetings",
    # lifestyle / people fluff
    "yoga", "meditation", "baby", "babies", "toddler", "newborn", "fashion",
    "dance", "dancing", "concert", "nightclub", "disco", "karaoke",
})


def _off_topic(query: str, meta) -> bool:
    """True when a clip's own tags/slug name a known off-topic stock subject (moon, lipstick, cat,
    sunset…) that the query never asked for — so an unrelated clip the API padded results with is
    dropped, while a clip whose subject the query DID request is kept. No metadata => never off-topic
    (we only drop on positive evidence)."""
    if isinstance(meta, (list, tuple)):
        meta = " ".join(str(m) for m in meta)
    meta_words = set(re.findall(r"[a-z]+", str(meta).lower()))
    stray = meta_words & _OFF_TOPIC_SUBJECTS
    if not stray:
        return False
    query_words = set(re.findall(r"[a-z]+", (query or "").lower()))
    return bool(stray - query_words)


_SLUG_STOP = frozenset({"http", "https", "www", "com", "pexels", "video", "videos", "photo", "photos"})


def _slug_words(url: str) -> str:
    """Pexels clips carry a descriptive page slug ('.../video/woman-applying-lipstick-123/'); flatten
    it to its DESCRIPTIVE words (dropping the domain boilerplate) for the relevance check. Other
    providers pass their tags directly."""
    words = re.findall(r"[a-z]+", (url or "").lower())
    return " ".join(w for w in words if w not in _SLUG_STOP)


def _clip_ok(query: str, meta, vocab: frozenset[str] | set[str]) -> bool:
    """Keep a candidate clip only when it is NOT an off-topic stock subject the query never asked for
    AND — when we know this video's vocabulary (``vocab``) — its tags/slug actually touch that
    vocabulary. The second check is what stops holiday/greeting/unrelated clips that dodge the
    denylist (e.g. a 'Happy Valentine's Day' clip in a software video). No tags/slug => keep (we only
    drop on positive evidence)."""
    if _off_topic(query, meta):
        return False
    if not vocab:
        return True
    if isinstance(meta, (list, tuple)):
        meta = " ".join(str(m) for m in meta)
    meta_words = set(re.findall(r"[a-z]+", str(meta).lower()))
    if not meta_words:
        return True
    return bool(meta_words & vocab)


@runtime_checkable
class BrollClient(Protocol):
    enabled: bool

    def search(self, query: str, *, context: str = "") -> list[str]:
        """Return candidate downloadable clip URLs for the query (best first; [] if no match).

        ``context`` is an optional bag of words describing the whole video; clips whose tags touch
        nothing in it are dropped."""
        ...

    def download(self, url: str) -> bytes: ...


class NullBrollClient:
    """Used when no Pexels key is configured — every scene falls back to generation/card."""

    enabled = False

    def search(self, query: str, *, context: str = "") -> list[str]:
        return []

    def download(self, url: str) -> bytes:  # pragma: no cover - never called when disabled
        raise RuntimeError("B-roll is disabled")


class PexelsBrollClient:
    enabled = True
    name = "pexels"
    _SEARCH_URL = "https://api.pexels.com/videos/search"

    def __init__(self, api_key: str, pool_size: int = 15, *, rng: random.Random | None = None) -> None:
        self._api_key = api_key
        self._pool_size = max(1, pool_size)
        self._rng = rng or random.Random()

    def search(self, query: str, *, context: str = "") -> list[str]:
        import httpx

        resp = httpx.get(
            self._SEARCH_URL,
            headers={"Authorization": self._api_key},
            params={
                "query": query,
                "per_page": self._pool_size,
                "page": _pick_page(self._rng),
                "orientation": "landscape",
            },
            timeout=30,
        )
        resp.raise_for_status()
        vocab = set(re.findall(r"[a-z]{3,}", context.lower()))
        urls: list[str] = []
        for video in resp.json().get("videos", []):
            files = sorted(video.get("video_files", []), key=lambda f: f.get("width", 0))
            if files and _clip_ok(query, _slug_words(video.get("url", "")), vocab):
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

    def __init__(self, api_key: str, pool_size: int = 15, *, rng: random.Random | None = None) -> None:
        self._api_key = api_key
        self._pool_size = min(200, max(3, pool_size))  # Pixabay requires per_page in [3, 200]
        self._rng = rng or random.Random()

    def search(self, query: str, *, context: str = "") -> list[str]:
        import httpx

        resp = httpx.get(
            self._SEARCH_URL,
            params={
                "key": self._api_key,
                "q": query,
                "per_page": self._pool_size,
                "page": _pick_page(self._rng),
            },
            timeout=30,
        )
        resp.raise_for_status()
        vocab = set(re.findall(r"[a-z]{3,}", context.lower()))
        urls: list[str] = []
        for hit in resp.json().get("hits", []):
            renditions = hit.get("videos", {})
            for size in ("large", "medium", "small", "tiny"):
                link = (renditions.get(size) or {}).get("url")
                if link:
                    if _clip_ok(query, hit.get("tags", ""), vocab):
                        urls.append(link)
                    break
        return urls

    def download(self, url: str) -> bytes:
        return _download_bytes(url)


class CoverrBrollClient:
    """Free stock video from Coverr (coverr.co). A third source so scenes draw from an even bigger,
    more varied pool. The key is requested at team@coverr.co, and Coverr asks that you attribute it
    (credit "Videos from Coverr"); it is therefore opt-in (empty key -> not used)."""

    enabled = True
    name = "coverr"
    _SEARCH_URL = "https://api.coverr.co/videos"

    def __init__(self, api_key: str, pool_size: int = 15, *, rng: random.Random | None = None) -> None:
        self._api_key = api_key
        self._pool_size = max(1, pool_size)
        self._rng = rng or random.Random()

    def search(self, query: str, *, context: str = "") -> list[str]:
        import httpx

        resp = httpx.get(
            self._SEARCH_URL,
            params={
                "api_key": self._api_key,
                "query": query,
                "page": _pick_page(self._rng, base=0),  # Coverr pages are 0-indexed
                "page_size": self._pool_size,
                "urls": "true",  # include the mp4 links in the list response
            },
            timeout=30,
        )
        resp.raise_for_status()
        vocab = set(re.findall(r"[a-z]{3,}", context.lower()))
        urls: list[str] = []
        for hit in resp.json().get("hits", []):
            link = (hit.get("urls") or {}).get("mp4")
            if link and _clip_ok(query, [hit.get("title", ""), hit.get("tags", "")], vocab):
                urls.append(link)
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

    def search(self, query: str, *, context: str = "") -> list[str]:
        pools: list[list[str]] = []
        for client in self._clients:
            try:
                pools.append(client.search(query, context=context))
            except Exception:  # one source failing must not sink the scene
                pools.append([])
        return _interleave(pools)

    def download(self, url: str) -> bytes:
        return _download_bytes(url)
