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
    redundancy_report,
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


def test_redundancy_flags_near_duplicate_scenes(good_script, make_script):
    assert redundancy_report(good_script)[0]  # distinct scenes pass
    dup = make_script({
        "title_options": ["t"], "hook": "FAANG pays well this year.",
        "scenes": [
            {"index": 0, "narration": "Staying calm in the interview matters more than raw coding speed for most candidates.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": None},
            {"index": 1, "narration": "Staying calm in the interview matters more than raw coding speed for most candidates.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": None},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [],
    })
    ok, detail = redundancy_report(dup)
    assert not ok and "scenes 1 & 2" in detail


def test_open_loop_report_passes_with_no_tease_or_declaration(good_script):
    from content_foundry.agents.judge_checks import open_loop_report

    ok, note = open_loop_report(good_script)  # no open_loop, no tease phrase -> the common, fine case
    assert ok and note == ""


def test_open_loop_report_flags_undelivered_declared_payoff(good_script):
    from content_foundry.agents.judge_checks import open_loop_report

    # A payoff is promised but its words never appear in the final scenes -> bait-and-switch -> fail.
    s = good_script.model_copy(update={"open_loop": "the geothermal calibration ritual"})
    ok, note = open_loop_report(s)
    assert not ok and "bait-and-switch" in note.lower()


def test_open_loop_report_passes_when_declared_payoff_is_delivered(good_script):
    from content_foundry.agents.judge_checks import open_loop_report

    scenes = list(good_script.scenes)
    last = scenes[-1].model_copy(update={
        "narration": scenes[-1].narration + " And here is the geothermal calibration ritual itself."
    })
    s = good_script.model_copy(update={
        "scenes": scenes[:-1] + [last], "open_loop": "the geothermal calibration ritual"
    })
    ok, note = open_loop_report(s)
    assert ok and note == ""  # promise words resurface at the end -> delivered


def test_open_loop_report_flags_a_tease_with_no_declared_payoff(good_script):
    from content_foundry.agents.judge_checks import open_loop_report

    scenes = list(good_script.scenes)
    first = scenes[0].model_copy(update={
        "narration": "Stick around, by the end of this video I will reveal it. " + scenes[0].narration
    })
    s = good_script.model_copy(update={"scenes": [first] + scenes[1:], "open_loop": ""})
    ok, note = open_loop_report(s)
    assert not ok and "open loop" in note.lower()



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
