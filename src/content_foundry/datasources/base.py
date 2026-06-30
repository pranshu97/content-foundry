"""DataSource protocol (Ch. 3.5).

Each concrete source fetches *and* normalizes its own payloads, returning
:class:`NormalizedSignal` objects so the Data Fetcher agent stays thin and network-free elsewhere.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import NormalizedSignal

DEFAULT_TIMEOUT = 30.0


@runtime_checkable
class DataSource(Protocol):
    name: str

    def fetch(self) -> list[NormalizedSignal]:
        """Fetch fresh signals. Raise on hard failure; the fetcher degrades gracefully."""
        ...
