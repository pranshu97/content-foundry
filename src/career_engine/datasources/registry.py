"""Build the set of enabled data sources from config (Ch. 3.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DataSource

if TYPE_CHECKING:
    from ..config import Settings


def build_sources(settings: Settings, niche: str | None = None) -> list[DataSource]:
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

    return sources
