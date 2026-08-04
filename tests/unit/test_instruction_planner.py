"""Agent 1.4 (Instruction Planner): decompose long-form --instructions into ROUTED directives."""

from __future__ import annotations

from content_foundry.agents.instruction_planner import (
    _MAX_ITEMS,
    _MAX_QUERIES,
    InstructionPlanner,
)
from content_foundry.models import InstructionPlan

_LONG = (
    "deliver actual grading rubrics rather than generic advice, explaining the distinct signals an "
    "interviewer looks for to justify an L4 vs a Senior placement. Explain the scoring matrix (Strong "
    "Hire, Lean Hire) and what those terms mean in a debrief. Show a mocked-up feedback form."
)


def test_planner_routes_instructions_into_buckets(settings, fakes):
    plan_json = {
        "research_focus": [
            "find the real scoring-matrix terms (Strong Hire, Lean Hire) and what they mean",
            "gather the negative signals interviewers record in a debrief",
        ],
        "research_queries": [
            "engineering interview scoring rubric Strong Hire Lean Hire meaning",
            "system design interview negative signals debrief",
        ],
        "script_directions": [
            "show a realistic mocked-up post-interview feedback form",
            "break down how a 45-minute conversation becomes a 3-paragraph verdict",
        ],
    }
    llm = fakes.LLM(script_json=plan_json)
    plan = InstructionPlanner(settings, llm).plan("ML system design interview", _LONG)
    assert isinstance(plan, InstructionPlan)
    assert plan.research_focus and plan.research_queries and plan.script_directions
    assert (
        "Strong Hire" in plan.research_queries[0]
    )  # a concrete searchable query, not the paragraph
    assert (
        "judge" not in llm.calls[-1]["system"].lower()
    )  # FakeLLM routes on 'judge'; never in prompt


def test_planner_empty_instructions_make_no_llm_call(settings, fakes):
    llm = fakes.LLM()
    plan = InstructionPlanner(settings, llm).plan("some idea", "   ")
    assert plan == InstructionPlan()  # empty, routed nowhere
    assert llm.call_count == 0  # a blank steer never calls the LLM


def test_planner_falls_back_to_verbatim_on_bad_json(settings, fakes):
    llm = fakes.LLM(script_json="this is not json at all")
    plan = InstructionPlanner(settings, llm).plan("idea", "Focus on remote roles; be concrete.")
    # A parse failure => the WHOLE instruction steers BOTH research and script, with no extra queries.
    assert plan.research_focus == ["Focus on remote roles; be concrete."]
    assert plan.script_directions == ["Focus on remote roles; be concrete."]
    assert plan.research_queries == []


def test_planner_caps_query_count_and_drops_blanks(settings, fakes):
    plan_json = {
        "research_focus": ["x", "", "  "],  # blanks dropped
        "script_directions": ["y"],
        "research_queries": [f"q{i}" for i in range(20)],  # over the cap
    }
    plan = InstructionPlanner(settings, fakes.LLM(script_json=plan_json)).plan("idea", "do things")
    assert plan.research_focus == ["x"]  # blank/whitespace items removed
    assert len(plan.research_queries) == _MAX_QUERIES


def test_planner_routes_the_richer_buckets(settings, fakes):
    """A long instruction usually hands over a running order ("then... conclude"), a set of contrasts
    ("X rather than Y") and the exact insider vocabulary. Flattening those into topic statements is
    what made plans read as a paraphrase of the input."""
    plan_json = {
        "research_focus": ["what each rating means"],
        "research_queries": ["scoring matrix ratings explained"],
        "script_directions": ["put the matrix on screen one row at a time"],
        "outline": ["name the frustration", "reveal the matrix", "what actually moves you up"],
        "avoid": ["that it is first-come-first-served", "that complaining helps"],
        "terminology": ["Bar Raiser", "acuity scale"],
    }
    plan = InstructionPlanner(settings, fakes.LLM(script_json=plan_json)).plan("idea", "do things")
    assert plan.outline == [
        "name the frustration",
        "reveal the matrix",
        "what actually moves you up",
    ]
    assert plan.avoid == ["that it is first-come-first-served", "that complaining helps"]
    assert plan.terminology == ["Bar Raiser", "acuity scale"]


def test_planner_caps_every_bucket(settings, fakes):
    """A runaway list would crowd out the rest of the research and script prompts."""
    plan_json = {
        "research_focus": [f"f{i}" for i in range(40)],
        "script_directions": ["y"],
        "avoid": [f"a{i}" for i in range(40)],
    }
    plan = InstructionPlanner(settings, fakes.LLM(script_json=plan_json)).plan("idea", "do things")
    assert len(plan.research_focus) == _MAX_ITEMS
    assert len(plan.avoid) == _MAX_ITEMS
