"""Agent 1 — Data Fetcher. Fetch + rank signals, then deterministically distill (Ch. 7)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..datasources import DataSource, build_sources
from ..errors import DataSourceError, InsufficientDataError, NoDataError
from ..logging import get_logger
from ..models import DataBrief, NormalizedSignal, Provenance
from . import distill


class DataFetcher:
    """Orchestrates source fetching (with cache) and deterministic distillation. No LLM."""

    def __init__(self, settings, repository=None, sources: Sequence[DataSource] | None = None):
        self._settings = settings
        self._repo = repository
        self._sources = sources
        self._log = get_logger(component="data_fetcher")

    def run(self, run_id: str, *, niche: str, topic_seed: str | None = None) -> DataBrief:
        sources = list(self._sources) if self._sources is not None else build_sources(
            self._settings, niche=niche, topic_seed=topic_seed
        )
        if not sources:
            raise NoDataError("No data sources are enabled/configured.")

        coverage: dict[str, bool] = {}
        gaps: list[str] = []
        all_signals: list[NormalizedSignal] = []

        for source in sources:
            try:
                signals = self._fetch_with_cache(source)
            except DataSourceError as exc:
                self._log.warning("source_failed", source=source.name, error=str(exc))
                coverage[source.name] = False
                gaps.append(f"{source.name}: {exc}")
                continue
            coverage[source.name] = bool(signals)
            if not signals:
                gaps.append(f"{source.name}: no signals returned")
            all_signals.extend(signals)

        if not all_signals:
            raise NoDataError("All data sources failed or returned nothing.")

        ranked = self._rank(self._dedup(all_signals), niche=niche, topic_seed=topic_seed)
        key_facts = distill.build_key_facts(ranked)
        if len(key_facts) < self._settings.min_facts:
            raise InsufficientDataError(
                f"Only {len(key_facts)} grounded facts (< MIN_FACTS={self._settings.min_facts})."
            )
        angles = distill.build_angles(ranked)

        return DataBrief(
            run_id=run_id,
            niche=niche,
            topic_seed=topic_seed,
            key_facts=key_facts,
            content_angles=angles,
            coverage=coverage,
            gaps=gaps,
            provenance=Provenance(
                produced_by="data_fetcher", model=None, config_hash=self._settings.config_hash
            ),
        )

    # ------------------------------------------------------------------ internals
    def _fetch_with_cache(self, source: DataSource) -> list[NormalizedSignal]:
        ttl = self._settings.signal_cache_ttl_min
        if self._repo is not None:
            cached = self._repo.get_cached_signals(source.name, ttl)
            if cached is not None:
                self._log.info("cache_hit", source=source.name, count=len(cached))
                return [NormalizedSignal.model_validate(c) for c in cached]

        signals = source.fetch()
        if self._repo is not None and signals:
            self._repo.put_cached_signals(
                source.name, [s.model_dump(mode="json") for s in signals]
            )
        return signals

    @staticmethod
    def _dedup(signals: Sequence[NormalizedSignal]) -> list[NormalizedSignal]:
        seen: set[tuple] = set()
        out: list[NormalizedSignal] = []
        for s in signals:
            key = (s.source, s.kind, s.title, s.value)
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out

    @staticmethod
    def _rank(
        signals: Sequence[NormalizedSignal], *, niche: str, topic_seed: str | None
    ) -> list[NormalizedSignal]:
        terms = [
            w
            for w in re.split(r"\W+", f"{niche} {topic_seed or ''}".lower())
            if len(w) > 2
        ]

        def relevance(sig: NormalizedSignal) -> int:
            haystack = f"{sig.title} {sig.value or ''}".lower()
            return sum(term in haystack for term in terms)

        return sorted(signals, key=lambda s: (relevance(s), s.observed_at), reverse=True)
