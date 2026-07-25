"""Thumbnail Director (Agent 5.6): the LLM writes a rich, per-video thumbnail image prompt."""

from __future__ import annotations

from content_foundry.agents.thumbnail_director import ThumbnailDirector, _sanitize
from content_foundry.config import get_settings, reset_settings_cache


def _settings(monkeypatch, *, enabled: bool):
    monkeypatch.setenv("THUMBNAIL_DIRECTOR_ENABLED", "true" if enabled else "false")
    reset_settings_cache()
    return get_settings()


def test_thumbnail_director_writes_prompt_from_the_description(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    text = "A cinematic thumbnail: a glowing laptop with a red REJECTED stamp, moody blue and orange."
    llm = fakes.LLM(script_json=text)
    out = ThumbnailDirector(settings, llm).compose(
        "some concept", title="A Title", niche="tech careers",
        description="FAANG interviewers grade you on a hidden scoring matrix and flag 'Hero Behavior'.",
    )
    assert out == text
    assert llm.call_count == 1
    user_prompt = llm.calls[-1]["prompt"]
    # the EXACT Gemini-style instruction + the raw description are the ONLY content input
    assert user_prompt.startswith(
        "Write a prompt to generate a thumbnail for a youtube video whose description is given below")
    assert "hidden scoring matrix" in user_prompt
    system = (llm.calls[-1]["system"] or "")
    assert "judge" not in system.lower()
    assert "EXAMPLE PROMPT" in system  # the worked example is included to drive the quality


def test_thumbnail_director_falls_back_to_concept_without_a_description(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a bold cinematic thumbnail")
    ThumbnailDirector(settings, llm).compose("a laptop with a red scorecard", title="t")
    # no description => the concept becomes the description-context handed to the model
    assert "a laptop with a red scorecard" in llm.calls[-1]["prompt"]


def test_thumbnail_director_disabled_is_noop(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=False)
    llm = fakes.LLM(script_json="unused")
    out = ThumbnailDirector(settings, llm).compose("x", title="y")
    assert out is None
    assert llm.call_count == 0  # disabled -> no LLM call at all


def test_thumbnail_director_adds_no_guardrails_or_avatar_details(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a bold cinematic thumbnail")
    ThumbnailDirector(settings, llm).compose(
        "developer at a desk", title="t", description="A video about ML system design interviews.")
    blob = ((llm.calls[-1]["system"] or "") + llm.calls[-1]["prompt"]).lower()
    # No operator face-matching / avatar guardrails are injected into the director prompt.
    assert "match the presenter" not in blob


def test_thumbnail_director_empty_concept_and_title_is_noop(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="x")
    out = ThumbnailDirector(settings, llm).compose("", title="")
    assert out is None
    assert llm.call_count == 0  # nothing to describe -> no LLM call


def test_thumbnail_director_returns_none_when_output_blank(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    out = ThumbnailDirector(settings, fakes.LLM(script_json="   ")).compose("c", title="t")
    assert out is None  # unusable (blank) model output -> caller falls back to the template


def test_sanitize_strips_fences_labels_and_quotes():
    assert _sanitize('```\nPrompt: "a dramatic scene, no text"\n```') == "a dramatic scene, no text"
    assert _sanitize("  a clean, glossy render  ") == "a clean, glossy render"
    assert _sanitize("") is None
    assert _sanitize("   ") is None


def test_sanitize_caps_length():
    out = _sanitize("word " * 600)
    assert out is not None and len(out) <= 1800
