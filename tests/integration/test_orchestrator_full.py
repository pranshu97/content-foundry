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


def test_augment_brief_prepends_research_facts(data_brief, fakes):
    from content_foundry.config import get_settings, reset_settings_cache
    from content_foundry.models import ResearchBrief, ResearchPoint

    reset_settings_cache()
    orch = Orchestrator(get_settings(), notifier=NullNotifier(), llm_provider=fakes.LLM())
    research = ResearchBrief(run_id="R", idea="x", points=[
        ResearchPoint(point="Referrals help 10-15x", evidence="10-15x", source_url="https://x/1")])
    merged = orch._augment_brief(data_brief, research)
    # research facts are PREPENDED (rank first) so the script grounds the idea, not the raw feed
    assert merged.key_facts[0].statement == "Referrals help 10-15x"
    assert len(merged.key_facts) == len(data_brief.key_facts) + 1
    assert orch._augment_brief(data_brief, None) is data_brief  # no research -> unchanged


def test_resume_reuses_saved_idea_and_research(monkeypatch, data_brief, fakes):
    from content_foundry.config import get_settings, reset_settings_cache
    from content_foundry.models import IdeaSelection, ResearchBrief, ResearchPoint
    from content_foundry.pipeline.artifacts import ensure_run_dirs, run_paths, save_model

    monkeypatch.setenv("RESEARCH_ENABLED", "true")
    reset_settings_cache()
    settings = get_settings()
    orch = Orchestrator(settings, notifier=NullNotifier(), llm_provider=fakes.LLM())
    paths = run_paths("7777", settings.output_dir)
    ensure_run_dirs(paths)
    # A prior run already chose an idea and researched it.
    save_model(
        IdeaSelection(run_id="7777", chosen="Interview prep in 30 days", source="custom"),
        paths.ideas,
    )
    save_model(
        ResearchBrief(run_id="7777", idea="Interview prep in 30 days",
                      points=[ResearchPoint(point="Referrals 10-15x", source_url="https://x/1")]),
        paths.research,
    )
    # Resuming generate reuses the saved pick (no re-brainstorm) and the saved research (no LLM call).
    idea = orch._resolve_idea("7777", paths, data_brief, "a different seed", [])
    assert idea == "Interview prep in 30 days"
    research = orch._run_research("7777", paths, data_brief, idea)
    assert research is not None and research.points[0].point == "Referrals 10-15x"
    # force bypasses the saved pick (brainstorm is off in tests, so it falls back to the raw seed).
    assert orch._resolve_idea("7777", paths, data_brief, "seed", [], force=True) == "seed"



def test_idea_chooser_picks_from_proposed(monkeypatch, data_brief, fakes):
    from content_foundry.config import get_settings, reset_settings_cache
    from content_foundry.models import IdeaSelection
    from content_foundry.pipeline.artifacts import ensure_run_dirs, load_model, run_paths

    monkeypatch.setenv("BRAINSTORM_ENABLED", "true")
    reset_settings_cache()
    settings = get_settings()
    seen: dict[str, list[str]] = {}

    def chooser(ideas: list[str]) -> str:
        seen["ideas"] = ideas
        return ideas[-1]  # deliberately pick the last

    orch = Orchestrator(
        settings, notifier=NullNotifier(),
        llm_provider=fakes.LLM(script_json=["Idea A", "Idea B", "Idea C"]),
        idea_chooser=chooser,
    )
    paths = run_paths("9999", settings.output_dir)
    ensure_run_dirs(paths)
    # --idea 'resume optimization' focuses the brainstormer; the chooser picks among the proposals.
    idea = orch._resolve_idea("9999", paths, data_brief, "resume optimization", [])
    assert seen["ideas"] == ["Idea A", "Idea B", "Idea C"]
    assert idea == "Idea C"
    # The generated ideas AND the exact pick are persisted to ideas.json for provenance.
    sel = load_model(IdeaSelection, paths.ideas, expected_stage="ideas")
    assert sel.generated == ["Idea A", "Idea B", "Idea C"]
    assert sel.chosen == "Idea C" and sel.chosen_index == 2
    assert sel.seed == "resume optimization" and sel.source == "brainstorm"
