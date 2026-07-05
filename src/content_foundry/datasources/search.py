"""General web-search data source (Ch. 3.5 extension).

Unlike the domain-specific feeds (jobs / layoffs / news / BLS), this source is decoupled and
domain-agnostic: it runs a plain web search on the run's *topic* (niche + seed), so ANY niche works.
Free by default via DuckDuckGo (no key, no signup); optionally Tavily or Brave when their free API
key is set. Each hit becomes a ``NormalizedSignal(kind="news")`` so the existing distiller turns it
into a grounded, citation-ready fact with the result's URL and snippet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

import httpx

from ..errors import DataSourceError
from ..models import NormalizedSignal, utcnow
from .base import DEFAULT_TIMEOUT

if TYPE_CHECKING:
    from ..config import Settings

_UA = "content-foundry/1.0 (+https://github.com/)"


class SearchResult(NamedTuple):
    title: str
    url: str | None
    snippet: str


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        """Return up to ``max_results`` web results for ``query``. Raise on hard failure."""
        ...


class DuckDuckGoProvider:
    """Free, key-less web search. Uses the ``ddgs`` library for full results when installed, and
    falls back to DuckDuckGo's key-less Instant Answer API so it still works with nothing installed."""

    name = "duckduckgo"

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        return self._via_library(query, max_results) or self._via_instant_answer(query, max_results)

    def _via_library(self, query: str, max_results: int) -> list[SearchResult]:  # pragma: no cover
        try:
            from ddgs import DDGS  # maintained package
        except ImportError:
            try:
                from duckduckgo_search import DDGS  # older name
            except ImportError:
                return []
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(query, max_results=max_results))
        except Exception:
            return []
        return [
            SearchResult(h.get("title", ""), h.get("href"), h.get("body", ""))
            for h in hits
            if h.get("title")
        ]

    def _via_instant_answer(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            resp = httpx.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "no_redirect": 1},
                headers={"User-Agent": _UA},
                timeout=DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # pragma: no cover - network
            raise DataSourceError(f"DuckDuckGo search failed: {exc}") from exc

        results: list[SearchResult] = []
        abstract = data.get("AbstractText")
        if abstract:
            results.append(SearchResult(data.get("Heading") or query, data.get("AbstractURL"), abstract))
        for topic in _flatten_related(data.get("RelatedTopics", [])):
            text = topic.get("Text")
            if text:
                results.append(SearchResult(text.split(" - ")[0][:120], topic.get("FirstURL"), text))
            if len(results) >= max_results:
                break
        return results[:max_results]


class TavilyProvider:
    """Tavily — a search API purpose-built for AI grounding (clean snippets). Free tier + key."""

    name = "tavily"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, max_results: int) -> list[SearchResult]:  # pragma: no cover - net
        try:
            resp = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": self._key, "query": query, "max_results": max_results},
                timeout=DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise DataSourceError(f"Tavily search failed: {exc}") from exc
        return _parse_tavily(data)


class BraveProvider:
    """Brave Search API — an independent real web index. Free tier + key."""

    name = "brave"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, max_results: int) -> list[SearchResult]:  # pragma: no cover - net
        try:
            resp = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
                timeout=DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise DataSourceError(f"Brave search failed: {exc}") from exc
        return _parse_brave(data)


def _parse_tavily(data: dict) -> list[SearchResult]:
    return [
        SearchResult(r.get("title", ""), r.get("url"), r.get("content", ""))
        for r in data.get("results", [])
        if r.get("title")
    ]


def _parse_brave(data: dict) -> list[SearchResult]:
    results = (data.get("web") or {}).get("results", [])
    return [
        SearchResult(r.get("title", ""), r.get("url"), r.get("description", ""))
        for r in results
        if r.get("title")
    ]


def _flatten_related(topics: list) -> list[dict]:
    """DuckDuckGo RelatedTopics nests groups ({"Topics": [...]}) alongside leaf results."""
    out: list[dict] = []
    for t in topics or []:
        if isinstance(t, dict) and "Topics" in t:
            out.extend(x for x in t["Topics"] if isinstance(x, dict))
        elif isinstance(t, dict):
            out.append(t)
    return out


class SearchSource:
    """A :class:`DataSource` that turns web-search hits into normalized signals."""

    name = "search"

    def __init__(self, provider: SearchProvider, query: str, *, max_results: int = 8) -> None:
        self._provider = provider
        self._query = (query or "").strip()
        self._max = max_results

    def fetch(self) -> list[NormalizedSignal]:
        if not self._query:
            return []
        try:
            results = self._provider.search(self._query, self._max)
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"Search ({self._provider.name}) failed: {exc}") from exc

        now = utcnow()
        signals: list[NormalizedSignal] = []
        for r in results:
            if not r.title:
                continue
            signals.append(
                NormalizedSignal(
                    source=self.name,
                    kind="news",  # a title + snippet + url — distilled via the "news" template
                    title=r.title.strip(),
                    value=None,
                    unit=None,
                    observed_at=now,
                    url=r.url,
                    raw={
                        "snippet": (r.snippet or r.title).strip(),
                        "provider": self._provider.name,
                        "query": self._query,
                    },
                )
            )
        return signals


def build_search_provider(settings: Settings) -> SearchProvider:
    """Pick the configured provider, gracefully falling back to key-less DuckDuckGo."""
    provider = (settings.search_provider or "duckduckgo").lower()
    if provider == "tavily" and settings.tavily_api_key:
        return TavilyProvider(settings.tavily_api_key)
    if provider == "brave" and settings.brave_api_key:
        return BraveProvider(settings.brave_api_key)
    return DuckDuckGoProvider()
