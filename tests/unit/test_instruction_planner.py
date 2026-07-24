"""Agent 1.4 (Instruction Planner): decompose long-form --instructions into ROUTED directives."""

from __future__ import annotations

from content_foundry.agents.instruction_planner import InstructionPlanner
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
    assert "Strong Hire" in plan.research_queries[0]  # a concrete searchable query, not the paragraph
    assert "judge" not in llm.calls[-1]["system"].lower()  # FakeLLM routes on 'judge'; never in prompt


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
    assert len(plan.research_queries) == 8  # capped at _MAX_QUERIES
