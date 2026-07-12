"""Agent 1.5 (Researcher): HTML->text extraction, best-effort fetch, LLM synthesis + fallback."""

from __future__ import annotations

import httpx
import respx

from content_foundry.agents.research import Researcher, _html_to_text, fetch_article_text


def test_html_to_text_prefers_paragraphs_and_unescapes():
    raw = (
        "<html><head><title>x</title></head><body>"
        "<nav><a>Home</a><a>Menu</a></nav><script>var a = 1;</script>"
        "<p>The mechanism is that ATS &amp; recruiters scan for keywords.</p>"
        "<style>.c{color:red}</style></body></html>"
    )
    text = _html_to_text(raw)
    assert "The mechanism is that ATS & recruiters scan for keywords." in text
    assert "var a" not in text and "color:red" not in text
    assert "Home" not in text and "Menu" not in text  # nav skipped in favour of the <p> body


def test_html_to_text_falls_back_when_no_paragraphs():
    assert _html_to_text("<div>Just a div, no paragraphs here</div>") == "Just a div, no paragraphs here"


@respx.mock
def test_fetch_article_text_extracts_body():
    respx.get("https://ex.com/a").mock(return_value=httpx.Response(
        200, html="<html><body><p>The mechanism is explained in depth here.</p></body></html>"))
    assert "The mechanism is explained in depth here." in fetch_article_text("https://ex.com/a")


@respx.mock
def test_fetch_article_text_returns_empty_on_http_error():
    respx.get("https://ex.com/bad").mock(return_value=httpx.Response(500))
    assert fetch_article_text("https://ex.com/bad") == ""


@respx.mock
def test_fetch_article_text_skips_non_html():
    respx.get("https://ex.com/data.json").mock(return_value=httpx.Response(
        200, json={"a": 1}, headers={"content-type": "application/json"}))
    assert fetch_article_text("https://ex.com/data.json") == ""


def test_researcher_synthesizes_points_from_fetched_sources(settings, data_brief, fakes, monkeypatch):
    monkeypatch.setattr(
        "content_foundry.agents.research.fetch_article_text",
        lambda url, **kwargs: f"full article body for {url}",
    )
    llm = fakes.LLM(script_json={"points": [
        {"point": "ATS filters on exact keywords", "explanation": "the first reader can't gauge skill",
         "evidence": "most resumes never reach a human", "source_url": "https://adzuna.example/1"},
        {"point": "Action verbs signal ownership", "explanation": "recruiter heuristics reward agency",
         "evidence": "", "source_url": None},
    ]})
    research = Researcher(settings, llm).run("R", data_brief, idea="resume tips")
    assert research.idea == "resume tips"
    assert [p.point for p in research.points] == [
        "ATS filters on exact keywords", "Action verbs signal ownership"]
    assert research.points[0].explanation.startswith("the first reader")
    assert research.used_model  # an LLM synthesized it
    assert research.source_urls  # pages were gathered from the brief's citations


def test_researcher_falls_back_to_snippets_when_llm_yields_no_points(settings, data_brief, fakes,
                                                                     monkeypatch):
    monkeypatch.setattr(
        "content_foundry.agents.research.fetch_article_text", lambda url, **kwargs: "some text")
    llm = fakes.LLM(script_json={"not": "an array of points"})  # nothing usable
    research = Researcher(settings, llm).run("R", data_brief, idea="x")
    assert research.used_model is None  # fell back deterministically
    assert research.points  # still produced points from the brief's facts
    assert research.points[0].point == data_brief.key_facts[0].statement


def test_researcher_fallback_when_no_source_urls(settings, fakes):
    from content_foundry.models import Citation, DataBrief, KeyFact, Provenance, utcnow

    brief = DataBrief(
        run_id="R", niche="tech careers",
        key_facts=[KeyFact(statement="A grounded fact", citation=Citation(
            source="search", url=None, observed_at=utcnow(), snippet="snip"))],
        provenance=Provenance(produced_by="data_fetcher"),
    )
    research = Researcher(settings, fakes.LLM()).run("R", brief, idea="x")
    assert research.used_model is None and research.source_urls == []
    assert research.points[0].point == "A grounded fact"


def test_research_key_facts_are_citable():
    from content_foundry.agents.research import research_key_facts
    from content_foundry.models import ResearchBrief, ResearchPoint

    research = ResearchBrief(run_id="R", idea="x", points=[
        ResearchPoint(point="Referrals give a 10-15x higher callback rate.",
                      explanation="recruiters trust warm intros", evidence="10-15x vs a cold apply",
                      source_url="https://x/1"),
        ResearchPoint(point="Portfolios beat degrees after 3 years.", source_url=None),
    ])
    facts = research_key_facts(research)
    assert [f.statement for f in facts] == [
        "Referrals give a 10-15x higher callback rate.", "Portfolios beat degrees after 3 years."]
    assert facts[0].citation.source == "research" and facts[0].citation.url == "https://x/1"
    assert facts[0].citation.snippet == "10-15x vs a cold apply"  # evidence -> the citable number
    assert facts[1].citation.snippet == "Portfolios beat degrees after 3 years."  # no evidence -> point
