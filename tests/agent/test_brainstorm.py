"""Agent 0 (Brainstormer) tests: LLM idea, deterministic fallback, anti-repeat (data collapse fix)."""

from __future__ import annotations

from content_foundry.agents import Brainstormer


def test_brainstorm_proposes_llm_ideas(settings, data_brief, fakes):
    llm = fakes.LLM(script_json=["3 data roles hiring", "Portfolio in a weekend", "Negotiate +15%"])
    ideas = Brainstormer(settings, llm).propose(data_brief, recent_ideas=[], count=3)
    assert ideas == ["3 data roles hiring", "Portfolio in a weekend", "Negotiate +15%"]
    assert llm.call_count == 1


def test_brainstorm_focus_seeds_fallback(settings, data_brief, fakes):
    # DEFAULT fake returns a script dict (no idea array) -> deterministic fallback, seeded by focus.
    ideas = Brainstormer(settings, fakes.LLM()).propose(
        data_brief, recent_ideas=[], count=5, focus="resume optimization"
    )
    assert ideas and any("resume optimization" in i.lower() for i in ideas)


def test_brainstorm_run_backcompat_returns_str(settings, data_brief, fakes):
    idea = Brainstormer(settings, fakes.LLM()).run(data_brief, recent_ideas=[])
    assert isinstance(idea, str) and idea


def test_brainstorm_avoids_recent(settings, data_brief, fakes):
    angles = [a.hook for a in data_brief.content_angles]
    if len(angles) < 2:
        return  # nothing to differentiate against
    ideas = Brainstormer(settings, fakes.LLM()).propose(
        data_brief, recent_ideas=[angles[0]], count=5
    )
    assert ideas and ideas[0] != angles[0]
