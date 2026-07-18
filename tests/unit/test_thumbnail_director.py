"""Thumbnail Director (Agent 5.6): the LLM writes a rich, per-video thumbnail image prompt."""

from __future__ import annotations

from content_foundry.agents.thumbnail_director import ThumbnailDirector, _sanitize
from content_foundry.config import get_settings, reset_settings_cache


def _settings(monkeypatch, *, enabled: bool):
    monkeypatch.setenv("THUMBNAIL_DIRECTOR_ENABLED", "true" if enabled else "false")
    reset_settings_cache()
    return get_settings()


def test_thumbnail_director_writes_prompt(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    text = "A shocked developer stares at a glowing red screen, dramatic rim lighting, no text"
    llm = fakes.LLM(script_json=text)
    out = ThumbnailDirector(settings, llm).compose(
        "developer shocked at screen", title="Why FAANG Rejects You", niche="tech careers"
    )
    assert out == text
    assert llm.call_count == 1
    system = llm.calls[-1]["system"]
    assert "developer shocked at screen" in system  # concept handed to the model
    assert "Why FAANG Rejects You" in system  # title handed to the model
    assert "judge" not in system.lower()  # must not misroute the shared fake / a real judge model


def test_thumbnail_director_disabled_is_noop(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=False)
    llm = fakes.LLM(script_json="unused")
    out = ThumbnailDirector(settings, llm).compose("x", title="y")
    assert out is None
    assert llm.call_count == 0  # disabled -> no LLM call at all


def test_thumbnail_director_no_person_asks_for_a_background(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="clean subject-free background, no text")
    ThumbnailDirector(settings, llm).compose("concept", title="t", no_person=True)
    assert "no people" in llm.calls[-1]["system"].lower()


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
    out = _sanitize("word " * 400)
    assert out is not None and len(out) <= 900
