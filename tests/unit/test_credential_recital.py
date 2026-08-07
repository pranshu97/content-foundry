"""The presenter's background must be WOVEN IN, never READ OUT.

`creator_bio` is a config string ("Senior Applied AI Scientist at Microsoft, previously Applied AI
Scientist at Amazon"). Run 0024 pasted it into scene 0 almost verbatim -- "As a Senior Applied AI
Scientist at Microsoft, previously an Applied Scientist at Amazon, I can tell you that..." -- which
reads as a CV recital rather than a person explaining how they know something.

Detection is a longest-consecutive-shared-word-run. CALIBRATED on real phrasings: recitals score
6-12, genuine earned authority scores 2-3, because a natural sentence borrows ONE role and rebuilds
the grammar around it.
"""

from __future__ import annotations

import pytest

from content_foundry.agents.judge_checks import _longest_shared_run, credential_recital_report

BIO = "Senior Applied AI Scientist at Microsoft, previously Applied AI Scientist at Amazon"


def _script(good_script, opening: str):
    s = good_script.model_copy(deep=True)
    s.scenes[0].narration = opening
    return s


@pytest.mark.parametrize(
    "opening",
    [
        # the exact line run 0024 shipped
        "As a Senior Applied AI Scientist at Microsoft, previously an Applied Scientist at Amazon, "
        "I can tell you that big tech cares about live traffic.",
        "Speaking as a Senior Applied AI Scientist at Microsoft, previously Applied AI Scientist "
        "at Amazon, here is the truth.",
    ],
)
def test_a_recited_bio_is_a_hard_fail(good_script, opening):
    ok, note = credential_recital_report(_script(good_script, opening), BIO)
    assert not ok
    assert "RECITAL" in note
    # the note must tell the writer what to do instead, not just that it failed
    assert "Name ONE role" in note


@pytest.mark.parametrize(
    "opening",
    [
        "And I know that number is wrong, because I spent years as an Applied Scientist at Amazon "
        "watching those dashboards during peak.",
        "When I was an Applied Scientist at Amazon, nobody once asked me to derive backprop.",
        "Every promo packet I reviewed at Microsoft told the same story.",
    ],
)
def test_naming_one_employer_in_your_own_words_is_fine(good_script, opening):
    """The gate must never punish the credential itself -- only quoting the config verbatim."""
    ok, note = credential_recital_report(_script(good_script, opening), BIO)
    assert ok, note


def test_a_blank_bio_can_never_fail(good_script):
    assert credential_recital_report(good_script, "") == (True, "")


def test_threshold_sits_above_every_legitimate_use_with_headroom():
    """Locks the calibration: legitimate phrasings must stay well under the cap."""
    legit = "because I spent years as an Applied Scientist at Amazon watching those dashboards"
    assert _longest_shared_run(BIO, legit) <= 3
    recital = "As a Senior Applied AI Scientist at Microsoft, previously an Applied Scientist"
    assert _longest_shared_run(BIO, recital) >= 6
