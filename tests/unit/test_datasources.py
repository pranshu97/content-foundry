"""Unit: data sources parse real-shaped payloads via respx (no network) (Ch. 22.5)."""

from __future__ import annotations

import httpx
import respx

from content_foundry.datasources.adzuna import AdzunaSource
from content_foundry.datasources.bls import BLSSource
from content_foundry.datasources.layoffs import LayoffsSource
from content_foundry.datasources.news import NewsSource
from content_foundry.datasources.registry import build_sources


@respx.mock
def test_adzuna_parses_postings_and_salary():
    respx.get(url__startswith="https://api.adzuna.com/v1/api/jobs/us/search/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1234,
                "results": [
                    {"title": "Junior Developer", "salary_min": 90000, "salary_max": 130000,
                     "created": "2026-06-28T00:00:00Z", "redirect_url": "https://adzuna/x"}
                ],
            },
        )
    )
    signals = AdzunaSource("id", "key", "tech").fetch()
    kinds = {s.kind for s in signals}
    assert "posting_trend" in kinds and "salary" in kinds
    salary = next(s for s in signals if s.kind == "salary")
    assert salary.value == "$110,000"


@respx.mock
def test_news_parses_articles():
    respx.get(url__startswith="https://newsapi.org/v2/everything").mock(
        return_value=httpx.Response(
            200,
            json={"articles": [
                {"title": "Hiring slows", "description": "d", "url": "https://n/1",
                 "publishedAt": "2026-06-28T00:00:00Z", "source": {"name": "Wire"}}
            ]},
        )
    )
    signals = NewsSource("key", "tech").fetch()
    assert signals[0].kind == "news"
    assert signals[0].raw["outlet"] == "Wire"


def test_news_without_key_returns_empty():
    assert NewsSource("", "tech").fetch() == []


@respx.mock
def test_layoffs_parses_rss_and_headcount():
    xml = (
        "<rss><channel>"
        "<item><title>BigCo lays off 1,200 employees</title>"
        "<link>https://l/1</link>"
        "<pubDate>Sat, 28 Jun 2026 00:00:00 GMT</pubDate>"
        "<description>restructuring</description></item>"
        "</channel></rss>"
    )
    respx.get("https://example.com/layoffs.rss").mock(return_value=httpx.Response(200, text=xml))
    signals = LayoffsSource("https://example.com/layoffs.rss").fetch()
    assert signals[0].kind == "layoff"
    assert signals[0].value == "1200"


@respx.mock
def test_bls_parses_latest_value():
    respx.post("https://api.bls.gov/publicAPI/v2/timeseries/data/").mock(
        return_value=httpx.Response(
            200,
            json={"Results": {"series": [
                {"seriesID": "LNS14000000",
                 "data": [{"year": "2026", "period": "M05", "value": "4.1"}]}
            ]}},
        )
    )
    signals = BLSSource().fetch()
    assert signals[0].kind == "outlook"
    assert signals[0].value == "4.1"


def test_registry_builds_enabled_sources(settings):
    sources = build_sources(settings, niche="tech careers")
    names = {s.name for s in sources}
    assert names == {"adzuna", "layoffs", "news"}
