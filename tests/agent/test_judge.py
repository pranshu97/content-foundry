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


def test_engagement_floorfree_wittiness_floored(settings, data_brief, good_script, fakes):
    report = Judge(settings, fakes.LLM()).run("R", good_script, data_brief, attempt_number=1)
    dims = {d.dimension: d for d in report.scores}
    assert "engagement" in dims and "wittiness" in dims and "ending" in dims
    # LLM-scored (from the fake judge JSON), and mapped 1-5 -> 0-10.
    assert dims["engagement"].score_1_5 == 4 and dims["engagement"].score == 7.5
    assert dims["wittiness"].score_1_5 == 4
    # engagement is a weighted contributor only; wittiness now carries a hard floor.
    assert dims["engagement"].minimum is None
    assert dims["wittiness"].minimum == settings.wittiness_min


def test_low_wittiness_revises_on_floor(settings, data_brief, good_script, fakes):
    # A grounded, complete, insightful script that simply is not funny enough must still REVISE.
    llm = fakes.LLM(judge_json={
        "actionability": {"justification": "ok", "evidence": "x", "score_1_5": 4},
        "insight": {"justification": "ok", "evidence": "x", "score_1_5": 4},
        "engagement": {"justification": "ok", "evidence": "x", "score_1_5": 4},
        "wittiness": {"justification": "dry", "evidence": "x", "score_1_5": 2},
    })
    report = Judge(settings, llm).run("R", good_script, data_brief, attempt_number=1)
    assert report.verdict == Verdict.REVISE
    wit = next(d for d in report.scores if d.dimension == "wittiness")
    assert wit.score < settings.wittiness_min


def test_ending_dimension_is_tracked(settings, data_brief, good_script, fakes):
    report = Judge(settings, fakes.LLM()).run("R", good_script, data_brief, attempt_number=1)
    ending = next(d for d in report.scores if d.dimension == "ending")
    assert ending.score == 10.0  # good_script closes with a subscribe nudge + a sign-off


def test_abrupt_ending_revises_on_floor(settings, data_brief, make_script, fakes):
    # Grounded, complete, and (per _TOP) funny + insightful — but it just STOPS: no CTA, no sign-off.
    payload = {
        "title_options": ["t"], "hook": "Layoffs hit 12,000 tech workers this year.",
        "scenes": [
            {"index": 0, "narration": "Google cut 12,000 tech roles this year, a real shift for juniors.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 0},
            {"index": 1, "narration": "Amazon added 8,000 cloud jobs, so the pivot is genuinely worth it.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 1},
            {"index": 2, "narration": "Microsoft pays 150,000 median, and that is simply where the market sits.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 2},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0, 1, 2],
    }
    report = Judge(settings, fakes.LLM(judge_json=_TOP)).run(
        "R", make_script(payload), data_brief, attempt_number=1
    )
    ending = next(d for d in report.scores if d.dimension == "ending")
    assert ending.score < settings.ending_min and not ending.passed
    assert report.verdict == Verdict.REVISE  # the abrupt close alone blocks the pass


def test_ending_needs_both_nudge_and_signoff(settings, data_brief, make_script, fakes):
    # One element is not enough: a sign-off with NO subscribe nudge is half credit and must REVISE,
    # and the revision feedback must name EXACTLY what is missing (not a blind retry).
    payload = {
        "title_options": ["t"], "hook": "Layoffs hit 12,000 tech workers this year.",
        "scenes": [
            {"index": 0, "narration": "Google cut 12,000 tech roles this year, a real shift for juniors.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 0},
            {"index": 1, "narration": "Amazon added 8,000 cloud jobs, so the pivot is genuinely worth it.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 1},
            {"index": 2, "narration": "Microsoft pays 150,000 median. That is the market, so see you next time.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 2},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0, 1, 2],
    }
    report = Judge(settings, fakes.LLM(judge_json=_TOP)).run(
        "R", make_script(payload), data_brief, attempt_number=1
    )
    ending = next(d for d in report.scores if d.dimension == "ending")
    assert ending.score == 5.0  # sign-off only -> half credit, below the floor
    assert report.verdict == Verdict.REVISE
    assert "NO like/subscribe nudge" in (report.revision_instructions or "")


def test_fail_when_attempts_exhausted(settings, data_brief, make_script, fakes):
    script = make_script(_UNGROUNDED)
    report = Judge(settings, fakes.LLM()).run("R", script, data_brief, attempt_number=3)
    assert report.verdict == Verdict.FAIL


# A single-scene draft that is otherwise well-formed: its one stat is grounded (fact_ref=0) and the
# disclosure is present, so grounding + compliance PASS. The quality rubric would score it well —
# only the completeness gate can catch that it is far too short to be a real video.
_STUB = {
    "title_options": ["t"],
    "hook": "Tech layoffs hit 12,000 workers last month.",
    "scenes": [
        {"index": 0, "narration": "Layoffs hit 12,000 last month, but the data says otherwise.",
         "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 0}
    ],
    "cta": "x", "description": "uses synthetic content", "tags": [],
    "thumbnail_concept": "x", "grounded_fact_refs": [0],
}


def test_short_stub_revises_on_completeness(settings, data_brief, make_script, fakes):
    llm = fakes.LLM()
    report = Judge(settings, llm).run("R", make_script(_STUB), data_brief, attempt_number=1)
    assert report.verdict == Verdict.REVISE
    assert report.grounding_score >= settings.grounding_min  # grounding did NOT fail
    assert llm.call_count == 0  # a stub is rejected deterministically — no tokens spent
    assert "LENGTH" in (report.revision_instructions or "")


# 18 narration words < the strict length floor (20 in tests), but every dimension is strong, so the
# weighted total clears gate_relief_score and the length floor is relaxed 20% -> PASS.
_NEAR_MISS = {
    "title_options": ["t"], "hook": "Layoffs hit 12,000 tech workers.",
    "scenes": [
        {"index": 0, "narration": "Google cut 12,000 tech roles here.",
         "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 0},
        {"index": 1, "narration": "Amazon added 8,000 cloud jobs today.",
         "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 1},
        {"index": 2, "narration": "Microsoft pays 150,000. Subscribe, see you.",
         "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 2},
    ],
    "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
    "grounded_fact_refs": [0, 1, 2],
}
_TOP = {
    "actionability": {"justification": "great", "evidence": "ship it", "score_1_5": 5},
    "insight": {"justification": "great", "evidence": "reframe", "score_1_5": 5},
    "engagement": {"justification": "great", "evidence": "hooks hard", "score_1_5": 5},
    "wittiness": {"justification": "great", "evidence": "funny", "score_1_5": 5},
}


def test_high_score_relaxes_length_gate(monkeypatch, data_brief, make_script, fakes):
    report = Judge(get_settings(), fakes.LLM(judge_json=_TOP)).run(
        "R", make_script(_NEAR_MISS), data_brief, attempt_number=1
    )
    assert report.weighted_total >= 9.0
    assert report.verdict == Verdict.PASS          # relaxed length floor cleared it
    assert "relaxed" in report.summary

    # With relief disabled, the identical near-miss is blocked on length.
    monkeypatch.setenv("GATE_RELIEF_SCORE", "11")
    reset_settings_cache()
    report2 = Judge(get_settings(), fakes.LLM(judge_json=_TOP)).run(
        "R", make_script(_NEAR_MISS), data_brief, attempt_number=1
    )
    assert report2.verdict == Verdict.REVISE
