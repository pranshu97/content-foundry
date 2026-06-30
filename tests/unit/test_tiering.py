"""Unit: model tiering — heavy vs light routing, behaviour-preserving defaults (future plan 2)."""

from __future__ import annotations

from types import SimpleNamespace

from content_foundry.providers.tiering import TaskTier, select_model


def _settings(*, enabled: bool, heavy="HEAVY", light="LIGHT"):
    return SimpleNamespace(llm_tiering_enabled=enabled, heavy_model=heavy, light_model=light)


def test_tiering_off_uses_caller_fallback():
    s = _settings(enabled=False)
    assert select_model(s, TaskTier.HEAVY, fallback="gen") == "gen"
    assert select_model(s, TaskTier.LIGHT, fallback="judge") == "judge"


def test_tiering_on_routes_by_tier():
    s = _settings(enabled=True)
    assert select_model(s, TaskTier.HEAVY, fallback="gen") == "HEAVY"
    assert select_model(s, TaskTier.LIGHT, fallback="judge") == "LIGHT"


def test_settings_model_properties_default_to_legacy(settings):
    # Empty MODEL_HEAVY/MODEL_LIGHT => behaviour identical to the pre-tiering models.
    assert settings.heavy_model == settings.generator_model
    assert settings.light_model == settings.judge_model


def test_script_generator_routes_heavy_then_light(monkeypatch, settings_with_tiers, data_brief, fakes):
    from content_foundry.agents import ScriptGenerator
    from content_foundry.templates import get_template

    llm = fakes.LLM(bad_then_good=True)
    ScriptGenerator(settings_with_tiers, llm).run("R", data_brief, get_template("contrarian"))
    assert llm.calls[0]["model"] == "heavy-model"  # initial generation
    assert llm.calls[1]["model"] == "light-model"  # mechanical JSON repair


def test_judge_scores_with_light_model(settings_with_tiers, good_script, data_brief, fakes):
    from content_foundry.agents import Judge

    llm = fakes.LLM()
    Judge(settings_with_tiers, llm).run("R", good_script, data_brief, attempt_number=1)
    assert llm.calls and all(c["model"] == "light-model" for c in llm.calls)
