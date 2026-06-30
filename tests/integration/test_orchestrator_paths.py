"""Integration: production gate, generate-only, and external-input resumability paths (Ch. 14)."""

from __future__ import annotations

import json
from pathlib import Path

from content_foundry.models import RunState
from content_foundry.notifications import NullNotifier
from content_foundry.pipeline.orchestrator import Orchestrator
from content_foundry.providers.youtube import DryRunPublisher


def test_production_gate_blocks_when_not_passed(settings, sample_signals, fakes, generic_payload):
    llm = fakes.LLM(
        script_json=generic_payload,
        judge_json={
            "actionability": {"justification": "w", "evidence": "x", "score_1_5": 1},
            "insight": {"justification": "c", "evidence": "x", "score_1_5": 1},
        },
    )
    orch = Orchestrator(
        settings, notifier=NullNotifier(), dry_run=True, llm_provider=llm,
        sources=[fakes.DataSource("adzuna", sample_signals)], tts_provider=fakes.TTS(),
        image_provider=None, render_backend=fakes.Render(), publisher=DryRunPublisher(),
    )
    result = orch.run(from_stage="fetch", to_stage="publish", niche="tech")
    # Judge FAILs -> the production gate must block stages 4-7.
    assert "voiceover" not in result.artifacts
    assert result.final_state == RunState.FAILED


def test_generate_only_stops_before_judge(settings, sample_signals, fakes):
    orch = Orchestrator(
        settings, notifier=NullNotifier(), llm_provider=fakes.LLM(),
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    result = orch.run(from_stage="fetch", to_stage="generate", niche="tech")
    assert "script" in result.artifacts
    assert "judge_report" not in result.artifacts
    assert result.final_state == RunState.GENERATED


def test_external_brief_input_is_consumed(settings, sample_signals, fakes):
    orch1 = Orchestrator(
        settings, notifier=NullNotifier(), llm_provider=fakes.LLM(),
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    r1 = orch1.run(from_stage="fetch", to_stage="fetch", niche="tech")
    brief_path = r1.artifacts["data_brief"]

    orch2 = Orchestrator(settings, notifier=NullNotifier(), llm_provider=fakes.LLM())
    r2 = orch2.run(from_stage="generate", to_stage="generate", input_path=brief_path, niche="tech")
    assert "script" in r2.artifacts
    brief = json.loads(Path(r2.artifacts["data_brief"]).read_text(encoding="utf-8"))
    assert brief["provenance"]["produced_by"] == "operator_edited"
