"""Optional U.S. BLS occupation-outlook baseline (Ch. 3.5)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from ..errors import DataSourceError
from ..models import NormalizedSignal, utcnow
from .base import DEFAULT_TIMEOUT

# Default series: civilian unemployment rate (a stable, public labor-market baseline).
_DEFAULT_SERIES = {"LNS14000000": "U.S. unemployment rate"}
_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


class BLSSource:
    name = "bls"

    def __init__(self, series: dict[str, str] | None = None, registration_key: str = "") -> None:
        self._series = series or dict(_DEFAULT_SERIES)
        self._registration_key = registration_key

    def fetch(self) -> list[NormalizedSignal]:
        payload: dict = {"seriesid": list(self._series)}
        if self._registration_key:
            payload["registrationkey"] = self._registration_key
        try:
            resp = httpx.post(_URL, json=payload, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise DataSourceError(f"BLS fetch failed: {exc}") from exc

        signals: list[NormalizedSignal] = []
        for series in data.get("Results", {}).get("series", []):
            series_id = series.get("seriesID", "")
            points = series.get("data", [])
            if not points:
                continue
            latest = points[0]
            label = self._series.get(series_id, series_id)
            signals.append(
                NormalizedSignal(
                    source=self.name,
                    kind="outlook",
                    title=label,
                    value=latest.get("value"),
                    unit="percent",
                    observed_at=_period_dt(latest),
                    url=None,
                    raw=latest,
                )
            )
        return signals


def _period_dt(point: dict):
    year = point.get("year")
    period = point.get("period", "M01")
    try:
        month = int(period.lstrip("M")) if period.startswith("M") else 1
        return datetime(int(year), max(1, min(12, month)), 1, tzinfo=UTC)
    except (ValueError, TypeError):
        return utcnow()
