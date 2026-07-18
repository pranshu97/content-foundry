"""Web-search data source: provider parsing, signal shaping, selection, registry (Ch. 3.5)."""

from __future__ import annotations

import httpx
import pytest
import respx

from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.datasources.registry import build_sources
from content_foundry.datasources.search import (
    BraveProvider,
    DuckDuckGoProvider,
    SearchResult,
    SearchSource,
    TavilyProvider,
    _parse_brave,
    _parse_tavily,
    build_search_provider,
)
from content_foundry.errors import DataSourceError


class _FakeProvider:
    name = "fake"

    def __init__(self, results):
        self._results = results
        self.queries: list[tuple[str, int]] = []

    def search(self, query, max_results):
        self.queries.append((query, max_results))
        return self._results[:max_results]


class _PerQueryProvider:
    """Returns query-specific results so fan-out / merge / dedupe can be verified."""

    name = "perquery"

    def __init__(self, mapping):
        self._mapping = mapping
        self.queries: list[str] = []

    def search(self, query, max_results):
        self.queries.append(query)
        return list(self._mapping.get(query, []))[:max_results]


def test_search_source_shapes_signals():
    provider = _FakeProvider([
        SearchResult("ML interview questions", "https://x/1", "A great snippet."),
        SearchResult("", "https://x/2", "no title -> skipped"),
        SearchResult("Second result", None, ""),
    ])
    sigs = SearchSource(provider, "  ml interviews  ", max_results=5).fetch()
    assert provider.queries == [("ml interviews", 5)]  # trimmed query passed through
    assert [s.title for s in sigs] == ["ML interview questions", "Second result"]  # blank skipped
    s0 = sigs[0]
    assert s0.source == "search" and s0.kind == "news"
    assert s0.url == "https://x/1"
    assert s0.raw["snippet"] == "A great snippet."
    assert s0.raw["provider"] == "fake"
    assert sigs[1].raw["snippet"] == "Second result"  # no snippet -> falls back to the title


def test_search_source_filters_offtopic_when_enabled():
    provider = _FakeProvider([
        SearchResult("ML Engineer Career Path 2026", "https://x/1", "salary and roles at FAANG"),
        SearchResult("Blox Fruits Values List 2026", "https://x/2", "all fruit and gamepass prices"),
        SearchResult("Fashion Trends Tokyo 2026", "https://x/3", "what people are wearing"),
        SearchResult("Applied Scientist vs Research", "https://x/4", "machine learning interview"),
    ])
    on = SearchSource(
        provider, "machine learning career at FAANG", max_results=8, filter_offtopic=True
    ).fetch()
    titles = [s.title for s in on]
    assert "ML Engineer Career Path 2026" in titles           # shares "career"/"faang"
    assert "Applied Scientist vs Research" in titles           # shares "machine"/"learning"
    assert "Blox Fruits Values List 2026" not in titles        # zero topic overlap -> dropped
    assert "Fashion Trends Tokyo 2026" not in titles           # zero topic overlap -> dropped
    # Gated: with the filter OFF (default) the junk is kept, proving it's opt-in.
    off = SearchSource(provider, "machine learning career at FAANG", max_results=8).fetch()
    assert "Blox Fruits Values List 2026" in [s.title for s in off]


def test_search_source_empty_query_returns_nothing():
    assert SearchSource(_FakeProvider([SearchResult("x", "u", "s")]), "   ").fetch() == []


def test_search_source_fans_queries_merges_and_dedupes():
    # Each facet is appended to the topic to form a distinct query; overlapping URLs are kept once.
    provider = _PerQueryProvider({
        "faang jobs": [
            SearchResult("Overview", "https://x/1", "base"),
            SearchResult("Shared", "https://x/dup", "in base too"),
        ],
        "faang jobs salary": [
            SearchResult("Shared", "https://x/dup", "duplicate url -> merged once"),
            SearchResult("Pay data", "https://x/2", "$150k median"),
        ],
        "faang jobs statistics": [
            SearchResult("Accept rate", "https://x/3", "1.5% of applicants"),
        ],
    })
    sigs = SearchSource(
        provider, "faang jobs", facets=["salary", "statistics"], max_results=5
    ).fetch()
    assert provider.queries == ["faang jobs", "faang jobs salary", "faang jobs statistics"]
    # Deduped by URL, order preserved across the fanned queries:
    assert [s.title for s in sigs] == ["Overview", "Shared", "Pay data", "Accept rate"]
    # Each signal records the specific angle that surfaced it:
    assert sigs[-1].raw["query"] == "faang jobs statistics"


def test_search_source_dedupes_blank_and_repeated_facets():
    provider = _PerQueryProvider({
        "topic": [SearchResult("Base", "https://x/1", "s")],
        "topic salary": [SearchResult("Sal", "https://x/2", "s")],
    })
    # Blank facets are dropped; a repeated facet collapses to a single query variant.
    sigs = SearchSource(provider, "topic", facets=["", "  ", "salary", "salary"]).fetch()
    assert provider.queries == ["topic", "topic salary"]  # base + one "topic salary"
    assert [s.title for s in sigs] == ["Base", "Sal"]


def test_search_source_survives_a_failing_query():
    class _FlakyProvider:
        name = "flaky"

        def __init__(self):
            self.queries: list[str] = []

        def search(self, query, max_results):
            self.queries.append(query)
            if query.endswith("salary"):
                raise RuntimeError("rate limited")
            return [SearchResult(f"R for {query}", f"https://x/{len(self.queries)}", "s")]

    sigs = SearchSource(
        _FlakyProvider(), "topic", facets=["salary", "stats"], max_results=3
    ).fetch()
    # The failing "topic salary" angle is skipped; the other two still contribute.
    assert [s.title for s in sigs] == ["R for topic", "R for topic stats"]


def test_search_source_raises_only_when_every_query_fails():
    class _DeadProvider:
        name = "dead"

        def search(self, query, max_results):
            raise RuntimeError("down")

    with pytest.raises(DataSourceError):
        SearchSource(_DeadProvider(), "topic", facets=["salary"]).fetch()



def test_tavily_parse_skips_titleless():
    results = _parse_tavily({"results": [
        {"title": "T1", "url": "https://t/1", "content": "c1"},
        {"title": "", "url": "https://t/2", "content": "skip"},
    ]})
    assert results == [SearchResult("T1", "https://t/1", "c1")]


def test_brave_parse():
    results = _parse_brave({"web": {"results": [
        {"title": "B1", "url": "https://b/1", "description": "d1"},
    ]}})
    assert results == [SearchResult("B1", "https://b/1", "d1")]


@respx.mock
def test_duckduckgo_search_falls_back_to_instant_answer(monkeypatch):
    # Force the library path to yield nothing so search() uses the key-less Instant Answer API.
    monkeypatch.setattr(DuckDuckGoProvider, "_via_library", lambda self, q, n: [])
    respx.get(url__startswith="https://api.duckduckgo.com/").mock(
        return_value=httpx.Response(200, json={
            "Heading": "Machine learning", "AbstractText": "ML is a field of AI.",
            "AbstractURL": "https://en.wikipedia.org/wiki/Machine_learning", "RelatedTopics": [],
        })
    )
    results = DuckDuckGoProvider().search("machine learning", 5)
    assert results[0].title == "Machine learning"
    assert results[0].url == "https://en.wikipedia.org/wiki/Machine_learning"


@respx.mock
def test_duckduckgo_instant_answer_flattens_related_topics():
    respx.get(url__startswith="https://api.duckduckgo.com/").mock(
        return_value=httpx.Response(200, json={
            "RelatedTopics": [
                {"Text": "Interview prep - study guides", "FirstURL": "https://x/rt1"},
                {"Topics": [{"Text": "Nested topic", "FirstURL": "https://x/rt2"}]},
            ],
        })
    )
    results = DuckDuckGoProvider()._via_instant_answer("q", 8)
    assert any(r.title == "Interview prep" for r in results)  # split on " - "
    assert any(r.url == "https://x/rt2" for r in results)  # nested topics flattened


def test_build_search_provider_selection(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "duckduckgo")
    reset_settings_cache()
    assert isinstance(build_search_provider(get_settings()), DuckDuckGoProvider)

    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    reset_settings_cache()
    assert isinstance(build_search_provider(get_settings()), TavilyProvider)

    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    reset_settings_cache()
    assert isinstance(build_search_provider(get_settings()), BraveProvider)


def test_keyed_provider_without_key_falls_back_to_duckduckgo(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")  # selected, but no key set
    reset_settings_cache()
    assert isinstance(build_search_provider(get_settings()), DuckDuckGoProvider)


def test_registry_builds_decoupled_search_source(monkeypatch):
    monkeypatch.setenv("ENABLED_SOURCES", "search")  # search-only, fully decoupled
    reset_settings_cache()
    settings = get_settings()
    sources = build_sources(settings, niche="ml careers", topic_seed="interviews")
    assert [s.name for s in sources] == ["search"]
    assert sources[0]._query == "ml careers interviews"  # topic = niche + seed (no network)
    # Multi-query fan-out is wired from config: base query + up to SEARCH_QUERY_COUNT-1 facets.
    expected_facets = settings.search_facets_list[: settings.search_query_count - 1]
    assert sources[0]._facets == expected_facets
    assert len(sources[0]._queries()) == 1 + len(expected_facets)

