"""Unit: deterministic judge checks + heuristics (Ch. 9.3a)."""

from __future__ import annotations

from content_foundry.agents.judge_checks import (
    compliance_check,
    freshness_and_fatigue,
    generic_hits,
    heuristic_actionability,
    heuristic_ending,
    heuristic_engagement,
    heuristic_insight,
    heuristic_wittiness,
    hook_score,
    specificity_score,
)


def test_specificity_good_beats_generic(good_script, generic_script):
    assert specificity_score(good_script) > specificity_score(generic_script)


def test_hook_score_rewards_specific_short_hook(good_script, generic_script):
    assert hook_score(good_script) > hook_score(generic_script)


def test_compliance_pass_and_fail(good_script):
    score, ok = compliance_check(good_script)
    assert ok and score == 10.0
    good_script.synthetic_disclosure = False
    score2, ok2 = compliance_check(good_script)
    assert not ok2 and score2 == 0.0


def test_generic_script_insight_below_floor(generic_script):
    assert heuristic_insight(generic_script) < 7.0
    assert generic_hits(generic_script) >= 3


def test_good_script_actionability_reasonable(good_script):
    assert heuristic_actionability(good_script) >= 4.0


def test_engagement_and_wittiness_beat_generic(good_script, generic_script):
    # generic, cliché-stuffed copy should read as less engaging and less witty than the grounded one.
    assert heuristic_engagement(good_script) > heuristic_engagement(generic_script)
    assert heuristic_wittiness(good_script) > heuristic_wittiness(generic_script)


def test_ending_rewards_cta_and_signoff(good_script, generic_script):
    # good_script closes with a subscribe nudge AND a sign-off; the generic one just stops.
    assert heuristic_ending(good_script) == 10.0
    assert heuristic_ending(generic_script) < heuristic_ending(good_script)


def test_fatigue_on_back_to_back_template(good_script):
    fresh = freshness_and_fatigue(
        "contrarian", good_script.hook, ["contrarian", "three_step"], []
    )
    assert fresh.fatigue is True


def test_no_fatigue_on_fresh_template(good_script):
    fresh = freshness_and_fatigue(
        "data_deep_dive", good_script.hook, ["contrarian", "three_step"], []
    )
    assert fresh.fatigue is False
    assert fresh.score > 5.0


def test_fatigue_on_similar_hook(good_script):
    fresh = freshness_and_fatigue(
        "data_deep_dive", good_script.hook, ["myth_vs_reality"], [good_script.hook]
    )
    assert fresh.fatigue is True
