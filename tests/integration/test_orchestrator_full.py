"""Integration: a full fetch→publish dry-run produces every artifact + package (Ch. 22.2)."""

from __future__ import annotations

from pathlib import Path

from content_foundry.models import RunState, Verdict
from content_foundry.notifications import NullNotifier
from content_foundry.pipeline.orchestrator import Orchestrator
from content_foundry.providers.youtube import DryRunPublisher


def test_full_pipeline_dry_run(settings, sample_signals, fakes):
    notifier = NullNotifier()
    orch = Orchestrator(
        settings,
        notifier=notifier,
        dry_run=True,
        llm_provider=fakes.LLM(),
        sources=[fakes.DataSource("adzuna", sample_signals)],
        tts_provider=fakes.TTS(with_timings=True),
        image_provider=None,
        broll_client=None,
        render_backend=fakes.Render(),
        publisher=DryRunPublisher(),
    )
    result = orch.run(from_stage="fetch", to_stage="publish", niche="tech careers",
                      topic_seed="junior developer hiring")

    assert result.final_state == RunState.PUBLISHED
    assert result.verdict == Verdict.PASS
    for key in ["data_brief", "script", "judge_report", "voiceover", "visuals", "video", "publish"]:
        assert key in result.artifacts, key
        assert Path(result.artifacts[key]).exists()
    assert result.package_path and Path(result.package_path).exists()
    assert "MANDATORY DISCLOSURE CHECKLIST" in Path(result.package_path).read_text(encoding="utf-8")
    assert result.video_url

    events = [c[0] for c in notifier.sent]
    assert "run_complete" in events
    assert "video_uploaded" in events
    assert "need_validation" in events

    run = orch.repo.get_run(result.run_id)
    assert run.state == "PUBLISHED"
    assert run.final_verdict == "PASS"
    attempts = orch.repo.get_attempts(result.run_id)
    assert attempts and attempts[-1].verdict == "PASS"


def test_render_artifact_written(settings, sample_signals, fakes):
    render = fakes.Render()
    orch = Orchestrator(
        settings, notifier=NullNotifier(), dry_run=True, llm_provider=fakes.LLM(),
        sources=[fakes.DataSource("adzuna", sample_signals)], tts_provider=fakes.TTS(),
        image_provider=None, broll_client=None, render_backend=render,
        publisher=DryRunPublisher(),
    )
    result = orch.run(from_stage="fetch", to_stage="render", niche="tech")
    assert render.calls == 1
    assert result.final_state == RunState.RENDERED
    video_mp4 = Path(result.artifacts["video"]).parent / "assets" / "video.mp4"
    assert video_mp4.exists()


def test_require_script_approval_pauses_then_resumes(monkeypatch, sample_signals, fakes):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("REQUIRE_SCRIPT_APPROVAL", "true")
    reset_settings_cache()
    s = get_settings()

    def make_orch():
        return Orchestrator(
            s, notifier=NullNotifier(), dry_run=True, llm_provider=fakes.LLM(),
            sources=[fakes.DataSource("adzuna", sample_signals)],
            tts_provider=fakes.TTS(with_timings=True), image_provider=None, broll_client=None,
            render_backend=fakes.Render(), publisher=DryRunPublisher(),
        )

    # A PASS pauses BEFORE production: state APPROVED, script written, no voiceover yet.
    paused = make_orch().run(from_stage="fetch", to_stage="publish", niche="tech careers")
    assert paused.verdict == Verdict.PASS
    assert paused.final_state == RunState.APPROVED
    assert "script" in paused.artifacts
    assert not (Path(paused.artifacts["script"]).parent / "voiceover.json").exists()

    # Signing off = resuming; production runs through to publish.
    resumed = make_orch().run(from_stage="voiceover", to_stage="publish", run_id=paused.run_id)
    assert resumed.final_state == RunState.PUBLISHED


def test_idea_chooser_picks_from_proposed(monkeypatch, data_brief, fakes):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("BRAINSTORM_ENABLED", "true")
    reset_settings_cache()
    seen: dict[str, list[str]] = {}

    def chooser(ideas: list[str]) -> str:
        seen["ideas"] = ideas
        return ideas[-1]  # deliberately pick the last

    orch = Orchestrator(
        get_settings(), notifier=NullNotifier(),
        llm_provider=fakes.LLM(script_json=["Idea A", "Idea B", "Idea C"]),
        idea_chooser=chooser,
    )
    # --idea 'resume optimization' focuses the brainstormer; the chooser picks among the proposals.
    idea = orch._resolve_idea(data_brief, "resume optimization", [])
    assert seen["ideas"] == ["Idea A", "Idea B", "Idea C"]
    assert idea == "Idea C"
