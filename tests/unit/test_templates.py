"""Unit: template registry + anti-fatigue selection (Ch. 16)."""

from __future__ import annotations

import random

import pytest

from content_foundry.templates import (
    ALL_TEMPLATES,
    TEMPLATES,
    get_template,
    pick_perspective_modifier,
    select_template,
)


def test_six_templates():
    assert len(TEMPLATES) == 6
    assert {"problem_solution", "myth_vs_reality", "three_step",
            "contrarian", "case_study", "data_deep_dive"} == set(TEMPLATES)


def test_get_template_unknown_raises():
    with pytest.raises(KeyError):
        get_template("does_not_exist")


def test_select_excludes_template():
    rng = random.Random(0)
    for _ in range(20):
        chosen = select_template(["contrarian"], exclude="contrarian", rng=rng)
        assert chosen.id != "contrarian"


def test_select_prefers_least_recently_used():
    # Every template used recently except one -> that one should dominate selections.
    recent = [t.id for t in ALL_TEMPLATES if t.id != "data_deep_dive"]
    counts = {t.id: 0 for t in ALL_TEMPLATES}
    rng = random.Random(42)
    for _ in range(200):
        counts[select_template(recent, rng=rng).id] += 1
    assert counts["data_deep_dive"] == max(counts.values())


def test_perspective_modifier_is_known():
    from content_foundry.templates import PERSPECTIVE_MODIFIERS

    assert pick_perspective_modifier(random.Random(1)) in PERSPECTIVE_MODIFIERS
