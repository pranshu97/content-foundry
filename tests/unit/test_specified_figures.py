"""A figure the CREATOR wrote into --instructions must not come back changed.

Run 0025 is the case these lock down. The brief said model code is "ten percent of the job"; the
script said "five percent" while KEEPING the matching "ninety percent", so the video shipped a claim
that contradicted both the instruction and itself -- and the thumbnail then faithfully rendered the
wrong number, which is what made it look like a thumbnail bug.

Instruction COVERAGE cannot catch this: the ask was covered and the topic discussed. Only the
quantity was silently rewritten.
"""

from __future__ import annotations

import pytest

from content_foundry.agents.judge_checks import _quantities, specified_figures_report

BRIEF = (
    "Begin with the hook that writing model code is only ten percent of the job while navigating "
    "production constraints is the remaining ninety percent. Emphasise strict latency budgets like "
    "twenty millisecond p99 limits, and the three-times productivity multiplier for engineers."
)


def _script(good_script, *narrations: str):
    """The fixture with its narration replaced, so only the text under test carries figures."""
    s = good_script.model_copy(deep=True)
    for i, scene in enumerate(s.scenes):
        scene.narration = narrations[i] if i < len(narrations) else ""
    s.hook = ""
    s.cta = ""
    return s


def test_the_real_run_0025_drift_is_caught(good_script):
    script = _script(
        good_script,
        "But model code is literally five percent of the actual production footprint; ninety "
        "percent is massive distributed systems and pipeline infrastructure.",
        "You must hold a twenty millisecond p99 budget, and seniors see a three-times multiplier.",
    )
    ok, note = specified_figures_report(script, BRIEF)
    assert not ok
    assert "10 percent" in note, note
    # The figures that DID survive must not be dragged into the complaint.
    assert "90 percent" not in note and "20 ms" not in note


def test_a_faithful_script_passes(good_script):
    script = _script(
        good_script,
        "Model code is only ten percent of the job; ninety percent is production systems.",
        "Hold a twenty millisecond p99 budget. Seniors get a three-times multiplier.",
    )
    ok, note = specified_figures_report(script, BRIEF)
    assert ok and note == ""


def test_digits_and_number_words_are_the_same_figure(good_script):
    """The writer spells numbers out for the TTS, so "10%" and "ten percent" must compare equal."""
    script = _script(
        good_script, "Model code is 10% of the job; 90% is production systems.", "20 ms p99, 3x."
    )
    ok, _ = specified_figures_report(script, BRIEF)
    assert ok


def test_a_figure_the_script_never_raises_is_not_flagged(good_script):
    """Narrow by design: silence is the coverage checker's problem, a WRONG number is this one's.

    Flagging every unmentioned figure would fire on almost every draft and train the operator to
    ignore the gate.
    """
    script = _script(good_script, "A script about nothing numeric at all.")
    ok, note = specified_figures_report(script, BRIEF)
    assert ok and note == ""


def test_extra_figures_the_writer_researched_are_never_penalised(good_script):
    script = _script(
        good_script,
        "Model code is ten percent of the job; ninety percent is production systems. Thirty "
        "percent of live requests hit silent nulls and canaries take one percent of traffic.",
        "Hold a twenty millisecond p99 budget. Seniors get a three-times multiplier.",
    )
    ok, _ = specified_figures_report(script, BRIEF)
    assert ok, "new researched figures must not be treated as a violation"


@pytest.mark.parametrize("brief", ["", "   ", "A brief with no figures in it whatsoever."])
def test_no_figures_specified_means_nothing_to_enforce(good_script, brief):
    script = _script(good_script, "Anything at all, ten percent even.")
    ok, note = specified_figures_report(script, brief)
    assert ok and note == ""


def test_years_and_decades_are_deliberately_excluded(good_script):
    """A decade is routinely paraphrased ("the 1970s" -> "the seventies"), so policing it is noise."""
    brief = "Use the ATM example from the 1970s and 1980s to prove the point."
    script = _script(good_script, "Back in the seventies, ATMs arrived.")
    ok, _ = specified_figures_report(script, brief)
    assert ok


def test_quantities_normalises_units_and_values():
    assert _quantities("ten percent") == {(10.0, "percent")}
    assert _quantities("10%") == {(10.0, "percent")}
    assert _quantities("20 ms") == _quantities("twenty milliseconds") == {(20.0, "ms")}
    assert _quantities("3x") == _quantities("three times") == {(3.0, "times")}
    assert _quantities("no numbers here") == set()
