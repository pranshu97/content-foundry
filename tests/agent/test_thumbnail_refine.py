"""Refinement is a TOUCH-UP PASS. It may repair phrases; it may never rewrite the prompt.

An earlier version asked the model to return a whole improved prompt, and it did what "improve"
invites: it threw away a rich cinematic scene (a FAANG engineering floor, a lit glass architecture
board, specific labels) and returned a plain white whiteboard with two words on it. That is strictly
worse than the draft it "fixed", and the operator was blunt about it.

So the contract changed shape: the model returns find/replace EDITS and the code applies them. A
rewrite is now structurally impossible rather than merely discouraged, and these tests pin the two
guards that make it so -- a per-edit word cap, and a floor on how much of the draft must survive.
"""

from __future__ import annotations

import json

import pytest

from content_foundry.agents.thumbnail_director import (
    _MAX_EDIT_FIND_WORDS,
    ThumbnailDirector,
    _apply_edits,
)
from content_foundry.config import get_settings, reset_settings_cache

DESCRIPTION = (
    "How FAANG evaluates ML engineers: latency budgets, scoring matrices, internal pivots."
)
# A deliberately RICH draft -- the thing refinement must not destroy.
DRAFT = (
    "A cinematic, dramatic YouTube thumbnail. The background is a dim high-tech engineering floor at "
    "a FAANG office with blue and warm orange ambient lights. In the foreground, a lit glass "
    "whiteboard shows a latency budget breakdown with sharp architecture diagrams. Clean negative "
    "space on the top-left for title overlay. --ar 16:9"
)


def _settings(monkeypatch, *, turns: int):
    monkeypatch.setenv("THUMBNAIL_DIRECTOR_ENABLED", "true")
    monkeypatch.setenv("THUMBNAIL_REFINE_TURNS", str(turns))
    reset_settings_cache()
    return get_settings()


class _LLM:
    def __init__(self, *replies: str):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def complete(self, prompt, *, system="", **kw):
        self.calls.append({"prompt": prompt, "system": system, **kw})
        text = self._replies.pop(0) if self._replies else DRAFT
        return type("R", (), {"text": text, "model": "m", "usage": {}})()


def _revise(*edits: dict) -> str:
    return json.dumps({"verdict": "revise", "edits": list(edits)})


def _keep() -> str:
    return json.dumps({"verdict": "keep", "edits": []})


# --------------------------------------------------------------- the edit engine
def test_an_edit_replaces_only_its_own_phrase():
    out, applied, skipped = _apply_edits(
        DRAFT, [{"find": "Clean negative space on the top-left for title overlay.", "replace": ""}]
    )
    assert applied == 1 and skipped == 0
    assert "negative space" not in out
    # everything else survives untouched
    assert "high-tech engineering floor" in out and "latency budget breakdown" in out


def test_an_edit_that_does_not_quote_the_draft_is_skipped():
    """The model paraphrased instead of copying, so there is nothing safe to change."""
    out, applied, skipped = _apply_edits(
        DRAFT, [{"find": "a phrase never in the draft", "replace": "x"}]
    )
    assert (applied, skipped) == (0, 1)
    assert out == " ".join(DRAFT.split())


def test_an_oversized_find_is_refused_because_that_is_a_rewrite():
    """A `find` big enough to swallow the draft is the rewrite loophole. It must be rejected."""
    whole = " ".join(DRAFT.split())
    out, applied, skipped = _apply_edits(
        whole, [{"find": whole, "replace": "A plain white board."}]
    )
    assert (applied, skipped) == (0, 1)
    assert out == whole
    assert len(whole.split()) > _MAX_EDIT_FIND_WORDS  # precondition: it really was oversized


def test_only_the_first_occurrence_is_replaced():
    text = "a red box and a red box"
    out, applied, _ = _apply_edits(text, [{"find": "red", "replace": "blue"}])
    assert out == "a blue box and a red box" and applied == 1


# --------------------------------------------------------------- the loop
def test_a_defect_is_repaired_without_touching_the_rest(monkeypatch):
    llm = _LLM(
        DRAFT,
        _revise({"find": "Clean negative space on the top-left for title overlay.", "replace": ""}),
        _keep(),
    )
    out = ThumbnailDirector(_settings(monkeypatch, turns=2), llm).compose(description=DESCRIPTION)
    assert "negative space" not in out
    assert "FAANG office" in out and "glass whiteboard" in out and "--ar 16:9" in out


def test_a_rewrite_smuggled_through_edits_is_rejected(monkeypatch):
    """Several small edits that between them gut the draft must not be accepted."""
    llm = _LLM(
        DRAFT,
        _revise(
            {"find": "The background is a dim high-tech engineering floor at", "replace": ""},
            {"find": "a FAANG office with blue and warm orange ambient lights.", "replace": ""},
            {"find": "In the foreground, a lit glass", "replace": ""},
            {
                "find": "whiteboard shows a latency budget breakdown with sharp architecture",
                "replace": "",
            },
            {
                "find": "diagrams. Clean negative space on the top-left for title overlay.",
                "replace": "",
            },
        ),
    )
    out = ThumbnailDirector(_settings(monkeypatch, turns=2), llm).compose(description=DESCRIPTION)
    assert out == " ".join(DRAFT.split()), "the gutted version should have been thrown away"


def test_it_stops_early_when_there_is_nothing_to_fix(monkeypatch):
    llm = _LLM(DRAFT, _keep(), _revise({"find": "cinematic", "replace": "flat"}))
    out = ThumbnailDirector(_settings(monkeypatch, turns=3), llm).compose(description=DESCRIPTION)
    assert out == DRAFT
    assert len(llm.calls) == 2, "should have stopped at the first 'keep'"


def test_zero_turns_ships_the_first_draft(monkeypatch):
    llm = _LLM(DRAFT, _revise({"find": "cinematic", "replace": "flat"}))
    out = ThumbnailDirector(_settings(monkeypatch, turns=0), llm).compose(description=DESCRIPTION)
    assert out == DRAFT and len(llm.calls) == 1


@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        "[1, 2, 3]",
        json.dumps({"verdict": "revise"}),  # no edits key
        json.dumps({"verdict": "revise", "edits": []}),  # nothing to do
        json.dumps({"verdict": "revise", "edits": "not a list"}),
        _revise({"find": "text that is absent", "replace": "x"}),  # nothing applies
    ],
    ids=["not-json", "list", "no-edits", "empty-edits", "edits-not-list", "no-match"],
)
def test_any_unusable_reply_keeps_the_draft(monkeypatch, reply):
    llm = _LLM(DRAFT, reply)
    out = ThumbnailDirector(_settings(monkeypatch, turns=2), llm).compose(description=DESCRIPTION)
    assert out == DRAFT


def test_a_raising_refine_call_keeps_the_draft(monkeypatch):
    class _Boom(_LLM):
        def complete(self, prompt, *, system="", **kw):
            self.calls.append({"prompt": prompt})
            if len(self.calls) == 1:
                return type("R", (), {"text": DRAFT, "model": "m", "usage": {}})()
            raise ValueError("provider exploded")

    out = ThumbnailDirector(_settings(monkeypatch, turns=2), _Boom()).compose(
        description=DESCRIPTION
    )
    assert out == DRAFT


def test_the_refiner_sees_both_the_description_and_the_draft(monkeypatch):
    llm = _LLM(DRAFT, _keep())
    ThumbnailDirector(_settings(monkeypatch, turns=1), llm).compose(description=DESCRIPTION)
    system = llm.calls[-1]["system"]
    assert DESCRIPTION in system and DRAFT in system
    assert "{description}" not in system and "{draft}" not in system, "unrendered token leaked"
    # It must be told not to simplify -- that instruction is what the earlier failure lacked.
    assert "NEVER SIMPLIFY" in system
    # conftest's FakeLLM routes on the word 'judge'; it must never appear in a generator-side prompt.
    assert "judge" not in system.lower()


def test_the_refiner_runs_much_cooler_than_the_draft(monkeypatch):
    llm = _LLM(DRAFT, _keep())
    ThumbnailDirector(_settings(monkeypatch, turns=1), llm).compose(description=DESCRIPTION)
    assert llm.calls[1]["temperature"] <= 0.2 < llm.calls[0]["temperature"]
