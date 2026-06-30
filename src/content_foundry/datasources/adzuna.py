"""Adzuna job-postings + salary source (Ch. 3.5)."""

from __future__ import annotations

import httpx
from dateutil import parser as dateparser

from ..errors import DataSourceError
from ..models import NormalizedSignal, utcnow
from .base import DEFAULT_TIMEOUT


class AdzunaSource:
    name = "adzuna"

    def __init__(
        self,
        app_id: str,
        app_key: str,
        query: str,
        *,
        country: str = "us",
        max_salary_signals: int = 5,
    ) -> None:
        self._app_id = app_id
        self._app_key = app_key
        self._query = query
        self._country = country
        self._max_salary_signals = max_salary_signals

    @property
    def _url(self) -> str:
        return f"https://api.adzuna.com/v1/api/jobs/{self._country}/search/1"

    def fetch(self) -> list[NormalizedSignal]:
        params = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "what": self._query,
            "results_per_page": 20,
            "content-type": "application/json",
        }
        try:
            resp = httpx.get(self._url, params=params, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise DataSourceError(f"Adzuna fetch failed: {exc}") from exc

        return self._parse(data)

    def _parse(self, data: dict) -> list[NormalizedSignal]:
        signals: list[NormalizedSignal] = []
        count = data.get("count")
        if count is not None:
            signals.append(
                NormalizedSignal(
                    source=self.name,
                    kind="posting_trend",
                    title=f"Open '{self._query}' postings",
                    value=str(count),
                    unit="open postings",
                    observed_at=utcnow(),
                    url=None,
                    raw={"count": count, "query": self._query},
                )
            )

        for result in data.get("results", []):
            smin, smax = result.get("salary_min"), result.get("salary_max")
            if not (smin or smax):
                continue
            midpoint = int(((smin or smax) + (smax or smin)) / 2)
            observed = result.get("created")
            signals.append(
                NormalizedSignal(
                    source=self.name,
                    kind="salary",
                    title=result.get("title", "role"),
                    value=f"${midpoint:,}",
                    unit="per year",
                    observed_at=_safe_dt(observed),
                    url=result.get("redirect_url"),
                    raw=result,
                )
            )
            if len([s for s in signals if s.kind == "salary"]) >= self._max_salary_signals:
                break
        return signals


def _safe_dt(value: object):
    if not value:
        return utcnow()
    try:
        return dateparser.parse(str(value))
    except (ValueError, TypeError):
        return utcnow()
