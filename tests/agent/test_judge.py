"""Agent 3 (Judge) tests: hard-gate short-circuit, insight floor, fatigue, JUDGE_MODE (Ch. 9)."""

from __future__ import annotations

from content_foundry.agents import Judge
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.models import Verdict

_UNGROUNDED = {
    "title_options": ["t"],
    "hook": "Salaries jumped 55% overnight.",
    "scenes": [
        {"index": 0, "narration": "Pay jumped 55% with no source at all.",
         "on_screen_text": None, "b_roll_keywords": [], "fact_ref": None}
    ],
    "cta": "x", "description": "uses synthetic content", "tags": [],
    "thumbnail_concept": "x", "grounded_fact_refs": [],
}


def test_grounding_violation_revises_without_llm(settings, data_brief, make_script, fakes):
    script = make_script(_UNGROUNDED)
    llm = fakes.LLM()
    report = Judge(settings, llm).run("R", script, data_brief, attempt_number=1)
    assert report.verdict == Verdict.REVISE
    assert llm.call_count == 0  # short-circuit: hard gate decided it
    assert report.grounding_score < settings.grounding_min


def test_generic_script_revises_on_insight_floor(settings, data_brief, generic_script, fakes):
    llm = fakes.LLM(judge_json={
        "actionability": {"justification": "weak", "evidence": "x", "score_1_5": 2},
        "insight": {"justification": "cliche", "evidence": "x", "score_1_5": 1},
    })
    report = Judge(settings, llm).run("R", generic_script, data_brief, attempt_number=1)
    assert report.verdict == Verdict.REVISE
    assert report.insight_score < settings.insight_min
    assert llm.call_count >= 1  # no hard gate failed -> LLM was consulted


def test_template_fatigue_forces_shift_no_llm(monkeypatch, data_brief, good_script, fakes):
    monkeypatch.setenv("JUDGE_MODE", "deterministic")
    reset_settings_cache()
    settings = get_settings()
    llm = fakes.LLM()
    report = Judge(settings, llm).run(
        "R", good_script, data_brief, attempt_number=1, recent_template_ids=["contrarian"]
    )
    assert report.template_fatigue and report.force_shift
    assert report.forced_template_id and report.forced_template_id != "contrarian"
    assert report.verdict == Verdict.REVISE
    assert llm.call_count == 0


def test_deterministic_mode_passes_good_script_zero_llm(monkeypatch, data_brief, good_script, fakes):
    monkeypatch.setenv("JUDGE_MODE", "deterministic")
    reset_settings_cache()
    settings = get_settings()
    llm = fakes.LLM()
    report = Judge(settings, llm).run("R", good_script, data_brief, attempt_number=1)
    assert llm.call_count == 0
    assert report.verdict == Verdict.PASS


def test_hybrid_pass(settings, data_brief, good_script, fakes):
    llm = fakes.LLM()
    report = Judge(settings, llm).run("R", good_script, data_brief, attempt_number=1)
    assert report.verdict == Verdict.PASS
    assert llm.call_count >= 1
    assert report.weighted_total >= settings.pass_threshold


def test_fail_when_attempts_exhausted(settings, data_brief, make_script, fakes):
    script = make_script(_UNGROUNDED)
    report = Judge(settings, fakes.LLM()).run("R", script, data_brief, attempt_number=3)
    assert report.verdict == Verdict.FAIL
