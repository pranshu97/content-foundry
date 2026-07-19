"""Unit: grounding + disclosure safeguards, incl. the hard publish gate (Ch. 9.3a, 13.4)."""

from __future__ import annotations

import pytest

from content_foundry.safeguards import (
    check_grounding,
    description_has_disclosure,
    disclosure_checklist,
    ensure_description_discloses,
    extract_stats,
    resolve_publish_outcome,
    ungrounded_scene_indices,
)


def test_extract_stats():
    assert "31%" in extract_stats("postings fell 31% this year")
    assert any("$" in s for s in extract_stats("median pay is $112,000"))
    assert extract_stats("just 3 steps") == []  # single digit ignored


def test_good_script_is_fully_grounded(good_script, data_brief):
    result = check_grounding(good_script, data_brief)
    assert result.fully_grounded
    assert result.score == 10.0


def test_ungrounded_stat_detected(make_script):
    payload = {
        "title_options": ["t"], "hook": "Salaries jumped 42% overnight!",
        "scenes": [{"index": 0, "narration": "Pay rose 42% with no source.",
                    "on_screen_text": None, "b_roll_keywords": [], "fact_ref": None}],
        "cta": "x", "description": "synthetic content note", "tags": [],
        "thumbnail_concept": "x", "grounded_fact_refs": [],
    }
    script = make_script(payload)
    # No DataBrief facts -> the 42% is ungrounded.
    from content_foundry.models import DataBrief, Provenance

    empty_brief = DataBrief(run_id="r", niche="n", key_facts=[],
                            provenance=Provenance(produced_by="data_fetcher"))
    result = check_grounding(script, empty_brief)
    assert not result.fully_grounded
    assert result.score < 8.0
    assert ungrounded_scene_indices(script, empty_brief) == [0]


def test_disclosure_detection_and_injection():
    assert description_has_disclosure("uses AI-altered/synthetic content")
    assert not description_has_disclosure("a normal description")
    injected = ensure_description_discloses("Plain text.")
    assert description_has_disclosure(injected)
    # Idempotent.
    assert ensure_description_discloses(injected) == injected


@pytest.mark.parametrize(
    "publish_mode,requested,disclosure,require_gate,expected",
    [
        # The critical contract: auto + public + no disclosure -> NEVER public.
        ("auto", "public", False, True, ("private", "pending_manual_disclosure")),
        ("auto", "public", False, False, ("private", "pending_manual_disclosure")),
        ("auto", "public", True, True, ("public", "uploaded")),
        ("draft", "private", True, True, ("private", "uploaded")),
        ("draft", "unlisted", True, True, ("unlisted", "uploaded")),
        ("draft", "private", False, True, ("private", "pending_manual_disclosure")),
        # Undisclosed UNLISTED is honored — the gate blocks only public, and unlisted is link-only
        # (never surfaced), so it needs no synthetic-content confirmation to upload.
        ("draft", "unlisted", False, True, ("unlisted", "pending_manual_disclosure")),
        ("auto", "unlisted", False, True, ("unlisted", "pending_manual_disclosure")),
    ],
)
def test_resolve_publish_outcome(publish_mode, requested, disclosure, require_gate, expected):
    assert (
        resolve_publish_outcome(
            publish_mode=publish_mode, requested_privacy=requested,
            disclosure_set=disclosure, require_manual_disclosure_before_public=require_gate,
        )
        == expected
    )


def test_disclosure_checklist_blocks_when_unset():
    assert "BLOCKING" in disclosure_checklist(False)
    assert "BLOCKING" not in disclosure_checklist(True)
