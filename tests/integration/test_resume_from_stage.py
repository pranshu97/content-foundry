"""Integration: stop after judge, hand-edit script.json, resume into production (Ch. 22.4 #5)."""

from __future__ import annotations

import json
from pathlib import Path

from career_engine.models import RunState, Verdict
from career_engine.notifications import NullNotifier
from career_engine.pipeline.orchestrator import Orchestrator
from career_engine.providers.youtube import DryRunPublisher


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
