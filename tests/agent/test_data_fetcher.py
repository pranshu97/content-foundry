"""Agent 1 tests: deterministic distill (no LLM), graceful degradation, cache TTL (Ch. 7)."""

from __future__ import annotations

import pytest

from content_foundry.agents import DataFetcher
from content_foundry.errors import InsufficientDataError, NoDataError
from content_foundry.models import NormalizedSignal, utcnow


def test_fetch_builds_grounded_brief_without_llm(settings, sample_signals, fakes):
    fake_llm = fakes.LLM()
    source = fakes.DataSource("adzuna", sample_signals)
    brief = DataFetcher(settings, None, [source]).run("R", niche="tech careers")
    # Deterministic: the fetcher never touches an LLM.
    assert fake_llm.call_count == 0
    assert len(brief.key_facts) == len(sample_signals)
    # Every fact value is copied from a real signal (order may change due to ranking).
    signal_values = {s.value for s in sample_signals}
    assert all(kf.value in signal_values for kf in brief.key_facts)
    assert brief.coverage["adzuna"] is True


def test_failing_source_degrades_gracefully(settings, sample_signals, fakes):
    sources = [fakes.DataSource("adzuna", sample_signals), fakes.FlakyDataSource("layoffs")]
    brief = DataFetcher(settings, None, sources).run("R", niche="tech")
    assert brief.coverage["layoffs"] is False
    assert any("layoffs" in gap for gap in brief.gaps)
    assert brief.key_facts  # still built from the healthy source


def test_all_sources_down_raises_no_data(settings, fakes):
    with pytest.raises(NoDataError):
        DataFetcher(settings, None, [fakes.FlakyDataSource("a"), fakes.FlakyDataSource("b")]).run(
            "R", niche="tech"
        )


def test_insufficient_facts_raises(settings, fakes):
    one = [NormalizedSignal(source="adzuna", kind="salary", title="Dev", value="$1",
                            observed_at=utcnow())]
    with pytest.raises(InsufficientDataError):
        DataFetcher(settings, None, [fakes.DataSource("adzuna", one)]).run("R", niche="tech")


def test_signal_cache_reused_within_ttl(settings, repo, sample_signals):
    class CountingSource:
        name = "adzuna"

        def __init__(self):
            self.count = 0

        def fetch(self):
            self.count += 1
            return sample_signals

    source = CountingSource()
    fetcher = DataFetcher(settings, repo, [source])
    fetcher.run("R1", niche="tech")
    fetcher.run("R2", niche="tech")
    assert source.count == 1  # second run served from signal_cache
