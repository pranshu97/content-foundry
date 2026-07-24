"""Build the set of enabled data sources from config (Ch. 3.5)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import DataSource

if TYPE_CHECKING:
    from ..config import Settings


# Facet angles that PIVOT the search to a different subject (career / lifestyle / money) rather than
# DEEPENING the idea's own subject — used only when the idea's OWN keywords actually ask for them.
_PIVOT_FACET_WORDS = frozenset({
    "salary", "salaries", "pay", "compensation", "career", "careers", "recruitment", "recruiting",
    "hiring", "layoff", "layoffs", "day", "life", "lifestyle",
})
# Grammatical filler that must NOT count as an idea keyword (e.g. "The ML..." must not make a
# "day in THE life" facet look relevant via the shared word "the").
_IDEA_STOP = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at", "is", "are", "be", "by",
    "with", "from", "vs", "how", "what", "why", "who", "when", "which", "your", "you", "my", "our",
    "it", "its", "as", "that", "this", "into", "about", "get", "make",
})


def _select_facets(pool: list[str], count: int, *, idea: str) -> list[str]:
    """Up to ``count`` facets RELEVANT to the idea (NOT random). Angles the idea's OWN keywords ask for
    rank first; subject-shifting angles (salary / career / day-in-the-life) the idea did NOT ask for
    rank LAST; every other deepening angle keeps the pool's order. Take the top ``count`` — so when the
    budget is small the off-topic-prone angles are the first dropped. A pool that fits is used whole."""
    count = max(0, count)
    if count == 0 or not pool:
        return []
    if len(pool) <= count:
        return list(pool)
    idea_words = {w for w in re.findall(r"[a-z0-9]+", (idea or "").lower()) if w not in _IDEA_STOP}

    def rank(facet: str) -> int:
        words = set(re.findall(r"[a-z0-9]+", facet.lower()))
        if words & idea_words:
            return 0  # the idea explicitly names this angle -> most relevant
        if words & _PIVOT_FACET_WORDS:
            return 2  # a subject-shifting angle the idea didn't ask for -> least relevant
        return 1  # a neutral deepening angle

    order = sorted(range(len(pool)), key=lambda i: (rank(pool[i]), i))
    return [pool[i] for i in order[:count]]


def build_sources(
    settings: Settings, niche: str | None = None, topic_seed: str | None = None
) -> list[DataSource]:
    """Construct only the enabled, adequately-configured sources. Missing config ⇒ skip."""
    query = niche or settings.target_niche
    enabled = settings.enabled_sources_list
    sources: list[DataSource] = []

    if "adzuna" in enabled and settings.adzuna_app_id and settings.adzuna_app_key:
        from .adzuna import AdzunaSource

        sources.append(AdzunaSource(settings.adzuna_app_id, settings.adzuna_app_key, query))

    if "layoffs" in enabled and settings.layoffs_feed_url:
        from .layoffs import LayoffsSource

        sources.append(LayoffsSource(settings.layoffs_feed_url))

    if "news" in enabled and settings.newsapi_key:
        from .news import NewsSource

        sources.append(NewsSource(settings.newsapi_key, query))

    if "bls" in enabled:
        from .bls import BLSSource

        sources.append(BLSSource())

    if "search" in enabled:
        from .search import SearchSource, build_search_provider

        focus = " ".join(p for p in (query, topic_seed) if p).strip()
        # Fan the search across several angles anchored on the topic (base + up to N-1 facets), picked
        # from the pool by RELEVANCE to the idea (angles it asks for first; off-topic pivots dropped).
        facets = _select_facets(
            settings.search_facets_list, settings.search_query_count - 1, idea=focus
        )
        sources.append(
            SearchSource(
                build_search_provider(settings),
                focus,
                facets=facets,
                max_results=settings.search_max_results,
                filter_offtopic=settings.search_relevance_filter,
            )
        )

    return sources
