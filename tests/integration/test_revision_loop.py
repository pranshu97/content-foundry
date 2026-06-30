"""Integration: a never-passing script exhausts MAX_REVISIONS -> FAILED (Ch. 22.4 #4)."""

from __future__ import annotations

from content_foundry.models import RunState, Verdict
from content_foundry.notifications import NullNotifier
from content_foundry.pipeline.orchestrator import Orchestrator


def test_revision_loop_exhausts_to_failed(settings, sample_signals, fakes, generic_payload):
    llm = fakes.LLM(
        script_json=generic_payload,
        judge_json={
            "actionability": {"justification": "weak", "evidence": "x", "score_1_5": 1},
            "insight": {"justification": "cliche", "evidence": "x", "score_1_5": 1},
        },
    )
    orch = Orchestrator(
        settings, notifier=NullNotifier(), llm_provider=llm,
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    result = orch.run(from_stage="fetch", to_stage="judge", niche="tech")

    assert result.final_state == RunState.FAILED
    assert result.verdict == Verdict.FAIL
    assert len(orch.repo.get_attempts(result.run_id)) == settings.max_revisions
