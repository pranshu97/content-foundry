"""B-roll Director (Agent 5.5): the LLM rewrites per-scene B-roll queries for relevance + diversity."""

from __future__ import annotations

from content_foundry.agents.broll_director import BrollDirector
from content_foundry.config import get_settings, reset_settings_cache


def _settings(monkeypatch, *, enabled: bool):
    monkeypatch.setenv("BROLL_DIRECTOR_ENABLED", "true" if enabled else "false")
    reset_settings_cache()
    return get_settings()


def test_broll_director_applies_relevant_diverse_queries(monkeypatch, good_script, fakes):
    settings = _settings(monkeypatch, enabled=True)
    payload = {"scenes": [
        {"index": s.index, "queries": [f"shot {s.index} alpha", f"shot {s.index} beta"]}
        for s in good_script.scenes
    ]}
    llm = fakes.LLM(script_json=payload)
    out = BrollDirector(settings, llm).run(good_script)

    assert out.scenes[0].b_roll_keywords == ["shot 0 alpha", "shot 0 beta"]
    assert out.scenes[1].b_roll_keywords == ["shot 1 alpha", "shot 1 beta"]
    assert llm.call_count == 1
    # The WHOLE script is handed to the model so it can keep shots diverse across scenes.
    assert '"narration"' in llm.calls[-1]["system"]


def test_broll_director_caps_queries_at_max(monkeypatch, good_script, fakes):
    settings = _settings(monkeypatch, enabled=True)  # BROLL_DIRECTOR_MAX_QUERIES default = 6
    payload = {"scenes": [
        {"index": good_script.scenes[0].index, "queries": ["a", "b", "c", "d", "e", "f", "g"]}
    ]}
    out = BrollDirector(settings, fakes.LLM(script_json=payload)).run(good_script)
    assert out.scenes[0].b_roll_keywords == ["a", "b", "c", "d", "e", "f"]  # capped at 6


def test_broll_director_keeps_keywords_on_unusable_output(monkeypatch, good_script, fakes):
    settings = _settings(monkeypatch, enabled=True)
    original = list(good_script.scenes[0].b_roll_keywords)
    out = BrollDirector(settings, fakes.LLM(script_json={"unexpected": True})).run(good_script)
    assert out.scenes[0].b_roll_keywords == original  # no "scenes" -> generator keywords kept


def test_broll_director_disabled_is_noop(monkeypatch, good_script, fakes):
    settings = _settings(monkeypatch, enabled=False)
    original = list(good_script.scenes[0].b_roll_keywords)
    llm = fakes.LLM(script_json={"scenes": [{"index": 0, "queries": ["x"]}]})
    out = BrollDirector(settings, llm).run(good_script)
    assert out.scenes[0].b_roll_keywords == original
    assert llm.call_count == 0  # disabled -> no LLM call at all
