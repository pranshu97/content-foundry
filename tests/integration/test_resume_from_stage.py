"""Integration: stop after judge, hand-edit script.json, resume into production (Ch. 22.4 #5)."""

from __future__ import annotations

import json
from pathlib import Path

from content_foundry.models import RunState, Verdict
from content_foundry.notifications import NullNotifier
from content_foundry.pipeline.orchestrator import Orchestrator
from content_foundry.providers.youtube import DryRunPublisher


def test_resume_from_stage_uses_operator_edits(settings, sample_signals, fakes):
    # Phase 1 — run through the Judge (PASS).
    orch1 = Orchestrator(
        settings, notifier=NullNotifier(), llm_provider=fakes.LLM(),
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    r1 = orch1.run(from_stage="fetch", to_stage="judge", niche="tech careers")
    assert r1.verdict == Verdict.PASS
    run_id = r1.run_id
    script_path = Path(r1.artifacts["script"])

    # Phase 2 — operator hand-edits the script narration.
    data = json.loads(script_path.read_text(encoding="utf-8"))
    data["scenes"][0]["narration"] = "EDITED narration by the operator, still 31% grounded."
    script_path.write_text(json.dumps(data), encoding="utf-8")

    # Phase 3 — resume from voiceover (new orchestrator, same DB/output via env).
    orch2 = Orchestrator(
        settings, notifier=NullNotifier(), dry_run=True,
        tts_provider=fakes.TTS(with_timings=True), image_provider=None,
        render_backend=fakes.Render(), publisher=DryRunPublisher(),
    )
    r2 = orch2.run(run_id=run_id, from_stage="voiceover", to_stage="voiceover")
    assert r2.final_state == RunState.VOICED

    # The hand-edit was detected and provenance re-stamped.
    edited = json.loads(script_path.read_text(encoding="utf-8"))
    assert edited["provenance"]["produced_by"] == "operator_edited"

    # The voiceover used the edited narration.
    vo = json.loads(Path(r2.artifacts["voiceover"]).read_text(encoding="utf-8"))
    spoken = " ".join(w["word"] for w in vo["word_timings"])
    assert "EDITED" in spoken


def test_resume_generate_after_failure_no_attempt_collision(
    settings, sample_signals, fakes, generic_payload
):
    # Phase 1 — a never-passing script exhausts MAX_REVISIONS -> FAILED (attempts 1..N recorded).
    low_judge = {
        "actionability": {"justification": "x", "evidence": "x", "score_1_5": 1},
        "insight": {"justification": "x", "evidence": "x", "score_1_5": 1},
    }
    orch1 = Orchestrator(
        settings, notifier=NullNotifier(),
        llm_provider=fakes.LLM(script_json=generic_payload, judge_json=low_judge),
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    r1 = orch1.run(from_stage="fetch", to_stage="judge", niche="tech", run_id="RESUMEGEN")
    assert r1.final_state == RunState.FAILED
    assert len(orch1.repo.get_attempts("RESUMEGEN")) == settings.max_revisions

    # Phase 2 — resume from generate with a good model: must NOT hit the UNIQUE attempts key.
    orch2 = Orchestrator(
        settings, notifier=NullNotifier(), dry_run=True, llm_provider=fakes.LLM(),
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    r2 = orch2.run(run_id="RESUMEGEN", from_stage="generate", to_stage="judge")
    assert r2.verdict == Verdict.PASS
    # DB numbering continued past the prior attempts (no collision), so one more attempt exists.
    assert len(orch2.repo.get_attempts("RESUMEGEN")) == settings.max_revisions + 1


def test_resume_infers_stage_from_artifacts(settings):
    from content_foundry.cli import _infer_next_stage
    from content_foundry.pipeline.artifacts import run_paths

    paths = run_paths("INFER1", settings.output_dir)
    brief = paths.artifact("data_brief")
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("{}", encoding="utf-8")
    # A FAILED run that only got as far as the brief should retry from generation, not re-fetch.
    assert _infer_next_stage("INFER1") == "generate"

    paths.artifact("visuals").write_text("{}", encoding="utf-8")
    assert _infer_next_stage("INFER1") == "render"  # got to visuals -> resume at render

