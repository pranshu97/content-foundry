"""Every instruction the creator writes must survive into the plan.

A long brief loses its tail: the planner returns a perfectly reasonable-looking plan that simply
never mentions the last few things asked for, and a dropped instruction is invisible precisely
because nothing downstream complains. Two mechanisms stop that, and these pin both:

  * the per-bucket cap SCALES with how much was written, so length alone can never truncate;
  * coverage is checked deterministically, and whatever is still unmentioned is re-planned ON ITS
    OWN -- where it is the entire input instead of the last paragraph of a wall of text.

The repair pass RE-PLANS rather than pasting the raw sentence in, so the creator's unpolished
wording never reaches the script prompt.
"""

from __future__ import annotations

import json

from content_foundry.agents.instruction_planner import (
    _MAX_ASKS,
    _MAX_ITEMS_CEILING,
    _MIN_ITEMS,
    InstructionPlanner,
    _item_cap,
    _merge,
    requirements,
    uncovered,
)
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.models import InstructionPlan

IDEA = "How to become an ML engineer at FAANG"


def _settings(monkeypatch):
    monkeypatch.setenv("INSTRUCTION_PLANNER_ENABLED", "true")
    reset_settings_cache()
    return get_settings()


class _LLM:
    def __init__(self, *replies: dict):
        self._replies = list(replies)
        self.calls: list[str] = []

    def complete(self, prompt, *, system="", **kw):
        self.calls.append(system)
        payload = self._replies.pop(0) if self._replies else {}
        return type("R", (), {"text": json.dumps(payload), "model": "m", "usage": {}})()


def _asks(*items: str) -> dict:
    """The ask-enumeration reply, which `plan()` now requests BEFORE planning."""
    return {"asks": list(items)}


def _plan_calls(llm: _LLM) -> list[str]:
    """Just the planning calls -- call 0 is always ask enumeration."""
    return llm.calls[1:]


# ------------------------------------------------------------------ splitting
def test_requirements_splits_on_sentences_and_drops_fragments():
    text = "Cover the salary bands. Ok. Then explain the promotion ladder in detail.\nAlso mention RSUs please."
    reqs = requirements(text)
    assert len(reqs) == 3, reqs  # "Ok." is too short to be a requirement
    assert reqs[0].startswith("Cover the salary")
    assert reqs[-1].startswith("Also mention RSUs")


def test_requirements_is_empty_for_nothing():
    assert requirements("") == [] and requirements("   ") == []


# ------------------------------------------------------------------ the cap
def test_the_cap_grows_with_the_brief_but_stays_bounded():
    assert _item_cap(0) == _MIN_ITEMS  # a short brief never gets a padded plan
    assert _item_cap(3) == _MIN_ITEMS
    assert _item_cap(12) > _MIN_ITEMS, "a long brief must not be truncated to the short-brief cap"
    assert _item_cap(10_000) == _MAX_ITEMS_CEILING  # ...but never unbounded


# ------------------------------------------------------------------ coverage
def test_a_requirement_the_plan_never_mentions_is_reported():
    reqs = [
        "Explain how latency budgets are evaluated in the interview.",
        "Also cover the visa sponsorship timeline for candidates.",
    ]
    plan = InstructionPlan(research_focus=["how latency budgets are evaluated during interviews"])
    missed = uncovered(reqs, plan)
    assert len(missed) == 1 and "visa sponsorship" in missed[0]


def test_a_covered_requirement_is_not_reported():
    reqs = ["Explain how latency budgets are evaluated in the interview."]
    plan = InstructionPlan(
        script_directions=["walk through evaluating latency budgets in an interview setting"]
    )
    assert uncovered(reqs, plan) == []


def test_coverage_is_a_fraction_not_a_word_count():
    """A flat 'share N words' bar is trivial for a long sentence and impossible for a short one."""
    long_req = "Discuss " + " ".join(f"topic{i}" for i in range(30))
    plan = InstructionPlan(research_focus=["topic0 topic1 topic2"])  # 3 of 30 = clearly dropped
    assert uncovered([long_req], plan) == [long_req]


# ------------------------------------------------------------------ the loop
def test_a_dropped_instruction_triggers_a_focused_repair_pass(monkeypatch):
    instructions = (
        "Explain how latency budgets are evaluated in the interview loop. "
        "Also cover the visa sponsorship timeline that candidates face."
    )
    enumerated = _asks(
        "Explain how latency budgets are evaluated in the interview loop.",
        "Cover the visa sponsorship timeline that candidates face.",
    )
    first = {"research_focus": ["how latency budgets are evaluated in the interview loop"]}
    repair = {"research_focus": ["the visa sponsorship timeline candidates face"]}
    llm = _LLM(enumerated, first, repair)
    plan = InstructionPlanner(_settings(monkeypatch), llm).plan(IDEA, instructions)

    assert len(_plan_calls(llm)) == 2, "the uncovered ask should have been re-planned"
    # The repair pass sees ONLY what was missed, not the whole brief again.
    assert "visa sponsorship" in llm.calls[2] and "latency budgets" not in llm.calls[2]
    # Both survive, original first.
    assert any("latency" in s for s in plan.research_focus)
    assert any("visa" in s for s in plan.research_focus)


def test_a_fully_covered_plan_costs_no_repair_call(monkeypatch):
    instructions = "Explain how latency budgets are evaluated in the interview loop."
    llm = _LLM(
        _asks("Explain how latency budgets are evaluated in the interview loop."),
        {"research_focus": ["how latency budgets are evaluated in the interview loop"]},
    )
    InstructionPlanner(_settings(monkeypatch), llm).plan(IDEA, instructions)
    assert len(_plan_calls(llm)) == 1


def test_a_repair_that_adds_nothing_stops_the_loop(monkeypatch):
    """An unhelpful repair must not be retried until the pass budget is exhausted."""
    same = {"research_focus": ["something entirely unrelated to the ask"]}
    llm = _LLM(_asks("Cover the visa sponsorship timeline candidates face."), same, same, same)
    InstructionPlanner(_settings(monkeypatch), llm).plan(
        IDEA, "Cover the visa sponsorship timeline that candidates face in detail."
    )
    assert len(_plan_calls(llm)) == 2, "identical repair output should end the loop immediately"


# ------------------------------------------------------------------ ask enumeration
def test_a_single_sentence_can_hold_many_asks(monkeypatch):
    """The whole point: a brief is a PARAGRAPH, so punctuation cannot tell you the ask count.

    One real sentence from the operator's brief carried six separate things to deliver, which the
    sentence splitter counts as one.
    """
    sentence = (
        "Next, break down the two proven pathways in: the direct external application, which "
        "requires optimizing your resume for latency budgets, and the internal pivot method, where "
        "you enter as a backend engineer and lateral over by productionizing research models."
    )
    assert len(requirements(sentence)) == 1  # the crude split sees ONE
    llm = _LLM(_asks("a", "b", "c", "d", "e", "f"), {"research_focus": ["x"]})
    planner = InstructionPlanner(_settings(monkeypatch), llm)
    assert len(planner._enumerate_asks(sentence)) == 6  # the model sees SIX


def test_enumeration_falls_back_to_the_sentence_split(monkeypatch):
    """A reply with no `asks` key must not lose the coverage check entirely."""
    settings = _settings(monkeypatch)
    assert InstructionPlanner(settings, _LLM({"not_asks": [1]}))._enumerate_asks("Cover it.") == []

    # plan() still completes, using the sentence split instead of the enumeration.
    llm = _LLM({"not_asks": [1]}, {"research_focus": ["whatever the plan says"]})
    plan = InstructionPlanner(settings, llm).plan(IDEA, "Cover the salary bands in real detail.")
    assert plan.research_focus == ["whatever the plan says"]


def test_enumeration_is_capped(monkeypatch):
    llm = _LLM(_asks(*[f"ask {i}" for i in range(200)]))
    planner = InstructionPlanner(_settings(monkeypatch), llm)
    assert len(planner._enumerate_asks("a long brief")) == _MAX_ASKS


def test_enumeration_survives_a_raising_provider(monkeypatch):
    class _Boom:
        def complete(self, prompt, *, system="", **kw):
            raise ValueError("provider down")

    assert InstructionPlanner(_settings(monkeypatch), _Boom())._enumerate_asks("anything") == []


def test_a_failed_first_pass_still_degrades_to_verbatim(monkeypatch):
    class _Boom:
        calls: list[str] = []

        def complete(self, prompt, *, system="", **kw):
            raise ValueError("provider down")

    text = "Cover the salary bands and the promotion ladder in real detail."
    plan = InstructionPlanner(_settings(monkeypatch), _Boom()).plan(IDEA, text)
    assert plan.research_focus == [text] and plan.script_directions == [text]


def test_empty_instructions_make_no_call(monkeypatch):
    llm = _LLM()
    plan = InstructionPlanner(_settings(monkeypatch), llm).plan(IDEA, "   ")
    assert plan == InstructionPlan() and llm.calls == []


# ------------------------------------------------------------------ merging
def test_merge_keeps_originals_first_and_drops_repeats():
    base = InstructionPlan(research_focus=["alpha", "beta"])
    patch = InstructionPlan(research_focus=["BETA", "gamma"])
    out = _merge(base, patch, cap=10)
    assert out.research_focus == ["alpha", "beta", "gamma"], "case-insensitive dedupe, base first"


def test_merge_respects_the_cap():
    base = InstructionPlan(research_focus=[f"a{i}" for i in range(5)])
    patch = InstructionPlan(research_focus=[f"b{i}" for i in range(5)])
    assert len(_merge(base, patch, cap=6).research_focus) == 6
