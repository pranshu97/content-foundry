"""General web-search data source (Ch. 3.5 extension).

Unlike the domain-specific feeds (jobs / layoffs / news / BLS), this source is decoupled and
domain-agnostic: it runs a plain web search on the run's *topic* (niche + seed), so ANY niche works.
Free by default via DuckDuckGo (no key, no signup); optionally Tavily or Brave when their free API
key is set. Each hit becomes a ``NormalizedSignal(kind="news")`` so the existing distiller turns it
into a grounded, citation-ready fact with the result's URL and snippet.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

import httpx

from ..errors import DataSourceError
from ..logging import get_logger
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


_TOPIC_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at", "is", "are", "be", "by",
    "with", "from", "vs", "how", "what", "why", "who", "when", "which", "your", "you", "my", "our",
    "best", "top", "guide", "complete", "ultimate", "list", "new", "trends", "trend", "tips",
    "tricks", "things", "ways", "about", "into", "get", "make", "common", "mistakes", "requirements",
})


def _topic_terms(query: str) -> set[str]:
    """Meaningful (non-stopword, non-year) words that define the run's topic."""
    return {
        w
        for w in re.findall(r"[a-z0-9]+", (query or "").lower())
        if len(w) >= 2 and not w.isdigit() and w not in _TOPIC_STOPWORDS
    }


def _shares_topic(result: SearchResult, terms: set[str]) -> bool:
    """True when the hit's title/snippet shares at least one topic word (i.e. it's on-topic)."""
    hay = re.findall(r"[a-z0-9]+", f"{result.title} {result.snippet}".lower())
    return bool(terms.intersection(hay))


class SearchSource:
    """A :class:`DataSource` that turns web-search hits into normalized signals.

    Rather than issuing a single query, it *fans out*: the base topic query plus one
    facet-augmented variant per configured facet (e.g. ``"<topic> salary"``,
    ``"<topic> statistics"``). Results from every query are merged and de-duplicated by URL, so a
    run gathers many more *distinct*, number-rich facts instead of collapsing to a couple of
    near-duplicate headlines. Individual queries are resilient — one failing angle is logged and
    skipped; only a total wipe-out (every query errors) surfaces as a :class:`DataSourceError`.
    """

    name = "search"

    def __init__(
        self,
        provider: SearchProvider,
        query: str,
        *,
        facets: Sequence[str] = (),
        max_results: int = 8,
        filter_offtopic: bool = False,
    ) -> None:
        self._provider = provider
        self._query = (query or "").strip()
        self._facets = [f.strip() for f in facets if f and f.strip()]
        self._max = max_results
        self._filter_offtopic = filter_offtopic
        self._log = get_logger(component="search_source")

    def _queries(self) -> list[str]:
        """The base topic query followed by ``"<topic> <facet>"`` variants (deduped, in order)."""
        variants = [self._query, *(f"{self._query} {facet}" for facet in self._facets)]
        seen: set[str] = set()
        ordered: list[str] = []
        for variant in variants:
            cleaned = variant.strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                ordered.append(cleaned)
        return ordered

    def fetch(self) -> list[NormalizedSignal]:
        if not self._query:
            return []

        queries = self._queries()
        now = utcnow()
        signals: list[NormalizedSignal] = []
        seen_keys: set[str] = set()
        # Anchor relevance on the base topic (not the facet suffixes), so a "trends 2026" angle can't
        # justify an off-topic hit. Empty terms (e.g. a bare query) => keep everything.
        terms = _topic_terms(self._query) if self._filter_offtopic else set()
        dropped = 0
        failures = 0
        for query in queries:
            try:
                results = self._provider.search(query, self._max)
            except Exception as exc:  # a single angle failing must not sink the whole fetch
                failures += 1
                self._log.warning(
                    "search_query_failed",
                    provider=self._provider.name,
                    query=query,
                    error=str(exc),
                )
                continue
            for r in results:
                if not r.title:
                    continue
                if terms and not _shares_topic(r, terms):  # off-topic junk -> drop it
                    dropped += 1
                    continue
                key = (r.url or r.title).strip().lower()
                if key in seen_keys:  # same result surfaced by another angle — keep it once
                    continue
                seen_keys.add(key)
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
                            "query": query,
                        },
                    )
                )

        if dropped:
            self._log.info("search_offtopic_filtered", dropped=dropped, kept=len(signals))
        if not signals and failures == len(queries):  # every angle errored — the source is down
            raise DataSourceError(
                f"Search ({self._provider.name}) failed for all {failures} queries."
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
