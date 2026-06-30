"""Industry news / labor-market reports via NewsAPI (Ch. 3.5)."""

from __future__ import annotations

import httpx
from dateutil import parser as dateparser

from ..errors import DataSourceError
from ..models import NormalizedSignal, utcnow
from .base import DEFAULT_TIMEOUT

_LABOR_TERMS = "layoffs OR hiring OR \"job market\" OR salary OR \"labor market\""


class NewsSource:
    name = "news"
    _URL = "https://newsapi.org/v2/everything"

    def __init__(self, api_key: str, query: str, *, page_size: int = 10) -> None:
        self._api_key = api_key
        self._query = query
        self._page_size = page_size

    def fetch(self) -> list[NormalizedSignal]:
        if not self._api_key:
            return []
        params = {
            "q": f"{self._query} AND ({_LABOR_TERMS})",
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": self._page_size,
            "apiKey": self._api_key,
        }
        try:
            resp = httpx.get(self._URL, params=params, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise DataSourceError(f"NewsAPI fetch failed: {exc}") from exc

        signals: list[NormalizedSignal] = []
        for article in data.get("articles", []):
            title = article.get("title")
            if not title:
                continue
            signals.append(
                NormalizedSignal(
                    source=self.name,
                    kind="news",
                    title=title,
                    value=None,
                    unit=None,
                    observed_at=_safe_dt(article.get("publishedAt")),
                    url=article.get("url"),
                    raw={
                        "snippet": article.get("description") or title,
                        "outlet": (article.get("source") or {}).get("name"),
                    },
                )
            )
        return signals


def _safe_dt(value: object):
    if not value:
        return utcnow()
    try:
        return dateparser.parse(str(value))
    except (ValueError, TypeError):
        return utcnow()
