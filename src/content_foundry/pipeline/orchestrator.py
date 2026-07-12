"""Pipeline orchestrator — sequences stages, drives the revision loop, enforces resumability,
the production gate, persistence, and notifications (Ch. 14)."""

from __future__ import annotations

import contextlib
import random
import time
from pathlib import Path

from ulid import ULID

from ..agents import (
    Brainstormer,
    DataFetcher,
    Judge,
    Publisher,
    Renderer,
    Researcher,
    ScriptGenerator,
    Visuals,
    Voiceover,
)
from ..agents.judge_checks import redundancy_report
from ..agents.research import research_key_facts
from ..config import get_settings
from ..errors import BudgetExhaustedError, ContentFoundryError, SchemaValidationError
from ..logging import configure_logging, get_logger
from ..models import (
    DataBrief,
    IdeaSelection,
    JudgeReport,
    PublishResult,
    ResearchBrief,
    RunResult,
    RunState,
    Script,
    Verdict,
    VideoAsset,
    VisualPackage,
    VoiceoverAsset,
)
from ..notifications import CreditMonitor, build_notifier
from ..persistence import Repository, init_db, make_engine, make_session_factory
from ..providers import (
    build_broll_client,
    build_image_provider,
    build_llm_provider,
    build_publisher,
    build_render_backend,
    build_sfx_client,
    build_tts_provider,
)
from ..templates import get_template, pick_perspective_modifier, select_template
from .artifacts import (
    ARTIFACT_FILENAMES,
    ensure_run_dirs,
    load_model,
    next_run_id,
    run_paths,
    save_model,
    sha256_file,
)
from .package import build_package_md
from .stages import PRODUCTION_STAGES, stages_between

_UNSET = object()

_STAGE_LABELS = {
    "voiceover": "Voiceover (TTS)",
    "visuals": "Visuals & thumbnail",
    "render": "Rendering video",
    "publish": "Publishing",
}

ARTIFACT_MODELS: dict[str, tuple[type, str]] = {
    "data_brief": (DataBrief, "data_brief"),
    "script": (Script, "script"),
    "judge_report": (JudgeReport, "judge_report"),
    "voiceover": (VoiceoverAsset, "voiceover"),
    "visuals": (VisualPackage, "visuals"),
    "video": (VideoAsset, "video"),
    "publish": (PublishResult, "publish"),
}


class Orchestrator:
    def __init__(
        self,
        settings=None,
        *,
        repository: Repository | None = None,
        notifier=None,
        dry_run: bool = False,
        llm_provider=None,
        sources=None,
        tts_provider=None,
        image_provider=_UNSET,
        broll_client=None,
        render_backend=None,
        publisher=None,
        sfx_client=None,
        reporter=None,
        idea_chooser=None,
    ) -> None:
        self.s = settings or get_settings()
        self._reporter = reporter
        self._idea_chooser = idea_chooser
        # With a live progress reporter (the CLI), keep the console quiet so it doesn't fight the
        # spinner — surface only real errors; the reporter shows the human-friendly progress.
        if reporter is not None:
            configure_logging(level="ERROR", fmt="console")
        else:
            configure_logging()
        self.log = get_logger(component="orchestrator")
        self.repo = repository or self._build_repo()
        self.notifier = notifier or build_notifier(self.s)
        self.dry_run = dry_run
        self._llm = llm_provider
        self._sources = sources
        self._tts = tts_provider
        self._image = image_provider
        self._broll = broll_client
        self._render = render_backend
        self._publisher = publisher
        self._sfx = sfx_client
        self.credit = CreditMonitor(
            self.notifier,
            budget_usd=self.s.monthly_budget_usd,
            threshold_pct=self.s.low_credit_threshold_pct,
            month_to_date_usd=float(self.repo.get_meta("spend_month", "0") or 0),
        )

    def _emit(self, event: str, **data) -> None:
        """Send a human-friendly progress event to the optional reporter (never raises)."""
        if self._reporter is not None:
            with contextlib.suppress(Exception):  # reporting must never break a run
                self._reporter(event, **data)

    # ============================================================= public API
    def run(
        self,
        *,
        run_id: str | None = None,
        from_stage: str = "fetch",
        to_stage: str = "publish",
        input_path: str | None = None,
        template_id: str | None = None,
        force: bool = False,
        niche: str | None = None,
        topic_seed: str | None = None,
        idea: str | None = None,
    ) -> RunResult:
        start = time.time()
        niche = niche or self.s.target_niche
        stages = stages_between(from_stage, to_stage)
        run_id = self._ensure_run(run_id, topic_seed)
        paths = run_paths(run_id, self.s.output_dir)
        ensure_run_dirs(paths)
        self.log.info("run_start", run_id=run_id, from_stage=from_stage, to_stage=to_stage)
        self._emit("start", run_id=run_id, from_stage=from_stage, to_stage=to_stage, niche=niche)

        produced: dict[str, object] = {}
        hashes: dict[str, str] = {}
        self._preload(run_id, from_stage, input_path, paths, produced, hashes)

        try:
            result = self._execute(
                run_id, stages, paths, produced, hashes,
                niche=niche, topic_seed=topic_seed, template_id=template_id, force=force, idea=idea,
            )
        except ContentFoundryError as exc:
            self.repo.update_run(run_id, state=RunState.FAILED.value)
            self._persist_spend()
            self.notifier.send(
                "run_failed", "❌ Run failed",
                f"{run_id}: {type(exc).__name__}: {exc}", meta={"run_id": run_id},
            )
            self.log.error("run_failed", run_id=run_id, error=str(exc))
            raise

        self._persist_spend()
        result.duration_sec = round(time.time() - start, 2)
        if result.final_state == RunState.FAILED:
            self.notifier.send(
                "run_failed", "❌ Run failed",
                f"{run_id}: ended in FAILED state", meta={"run_id": run_id},
            )
        else:
            self.notifier.send(
                "run_complete", "✅ Run complete",
                f"{run_id}: {result.final_state.value} "
                f"verdict={result.verdict.value if result.verdict else 'n/a'}",
                meta={"run_id": run_id},
            )
        self.log.info("run_end", run_id=run_id, state=result.final_state.value)
        self._emit(
            "end", run_id=run_id, state=result.final_state.value,
            verdict=result.verdict.value if result.verdict else None,
        )
        return result

    # ============================================================== execution
    def _execute(
        self, run_id, stages, paths, produced, hashes, *, niche, topic_seed, template_id, force,
        idea=None,
    ) -> RunResult:
        run_root = paths.root
        verdict = self._latest_verdict(run_id, produced)
        handled_judge = False
        gate_checked = False
        self._check_budget(run_id)

        for stage in stages:
            if stage == "fetch":
                self._emit("step", label="Fetching labor-market data")
                brief = DataFetcher(self.s, self.repo, self._sources).run(
                    run_id, niche=niche, topic_seed=topic_seed or idea
                )
                self._persist(run_id, "data_brief", brief, paths, produced, hashes, None, {})
                self.repo.update_run(run_id, state=RunState.FETCHED.value)
                live = ", ".join(s for s, ok in brief.coverage.items() if ok) or "none"
                self._emit("done", label="Data brief",
                           detail=f"{len(brief.key_facts)} facts · {live}")

            elif stage == "generate":
                if "judge" in stages:
                    verdict = self._gen_judge_loop(
                        run_id, paths, produced, hashes, template_id=template_id, idea=idea,
                        force=force,
                    )
                    handled_judge = True
                else:
                    self._generate_once(
                        run_id, paths, produced, hashes, template_id=template_id, idea=idea,
                        force=force,
                    )

            elif stage == "judge":
                if handled_judge:
                    continue
                verdict = self._judge_once(run_id, paths, produced, hashes)

            elif stage in PRODUCTION_STAGES:
                if not gate_checked:
                    if not (force or verdict == Verdict.PASS):
                        self.log.info("production_gate_blocked", run_id=run_id, verdict=str(verdict))
                        self._emit("gate", ok=False, verdict=verdict.value if verdict else None)
                        break
                    if self.s.require_script_approval and handled_judge and not force:
                        self.log.info("awaiting_script_approval", run_id=run_id)
                        self._emit("gate", ok=False, awaiting_approval=True, run_id=run_id)
                        break
                    self._emit("gate", ok=True)
                    gate_checked = True
                self._run_production_stage(
                    stage, run_id, run_root, paths, produced, hashes, force=force
                )

        self._assemble_package(run_id, paths, produced)
        return self._build_result(run_id, stages, paths, produced)

    # ----------------------------------------------------- generate <-> judge
    def _revise_reason(self, report, script) -> str | None:
        """Human-readable 'why not PASS' for the progress line. Covers the length/completeness gate
        (which is NOT a scored rubric dimension), failing hard floors, and template fatigue. Mirrors
        the Judge's high-score gate relief so the shown floor matches the one actually enforced."""
        if report.verdict == Verdict.PASS:
            return None
        s = self.s
        factor = 1.0 - (s.gate_relief_ratio if report.weighted_total >= s.gate_relief_score else 0.0)
        floor = int(s.min_script_word_ratio * s.script_target_words * factor)
        min_scenes = max(2, round(s.min_scenes * factor))
        bits: list[str] = []
        if len(script.scenes) < min_scenes or script.word_count < floor:
            bits.append(
                f"too short ({script.word_count} words, need {floor}+ for target "
                f"{s.script_target_words}, {len(script.scenes)} scenes)"
            )
        if not redundancy_report(script, threshold=s.max_scene_similarity)[0]:
            bits.append("duplicate scenes")
        for d in report.scores:
            if not d.passed and d.minimum is not None:
                bits.append(f"{d.dimension} {d.score:.1f}<{d.minimum:.0f}")
        if report.template_fatigue:
            bits.append("template fatigue")
        return "; ".join(bits) or f"score {report.weighted_total:.2f} < {self.s.pass_threshold}"

    def _resolve_idea(self, run_id, paths, brief, seed, recent_hooks, *, force=False) -> str:
        """The user's --idea (``seed``) FOCUSES the brainstormer rather than being used verbatim, so
        the script is a concrete, relevant video instead of generic niche filler. The Brainstormer
        proposes several ideas; an interactive chooser (CLI) picks one, else the first is used.
        Brainstorm off => the seed is used raw. Either way the ideas + the exact pick are recorded
        to ``ideas.json`` for provenance. On a resume (``ideas.json`` already exists) the saved pick
        is reused so re-running generate keeps the SAME video, unless ``force``."""
        seed = (seed or "").strip()
        if not force:
            saved = self._load_ideas(paths)
            if saved and saved.chosen:
                self._emit("done", label="Idea (reused)", detail=saved.chosen[:80])
                return saved.chosen
        if not self.s.brainstorm_enabled:
            self._record_idea(run_id, paths, seed=seed, generated=[], chosen=seed, source="seed")
            return seed
        ideas = Brainstormer(self.s, self._llm_provider()).propose(
            brief, recent_ideas=recent_hooks, count=self.s.brainstorm_idea_count, focus=seed
        )
        if not ideas:
            self._record_idea(run_id, paths, seed=seed, generated=[], chosen=seed, source="seed")
            return seed
        chosen = (self._idea_chooser(ideas) if self._idea_chooser else ideas[0]) or ideas[0]
        source = "brainstorm" if chosen in ideas else "custom"
        self._record_idea(run_id, paths, seed=seed, generated=ideas, chosen=chosen, source=source)
        self._emit("done", label="Idea", detail=chosen[:80])
        return chosen

    def _record_idea(self, run_id, paths, *, seed, generated, chosen, source) -> None:
        """Persist the brainstormed ideas + the exact pick to ``ideas.json`` for later inspection."""
        selection = IdeaSelection(
            run_id=run_id,
            seed=seed,
            brainstorm_enabled=self.s.brainstorm_enabled,
            source=source,
            generated=list(generated),
            chosen=chosen,
            chosen_index=(generated.index(chosen) if chosen in generated else -1),
        )
        save_model(selection, paths.ideas)

    def _run_research(self, run_id, paths, brief, idea, *, force=False) -> ResearchBrief | None:
        """Agent 1.5: synthesize a source-backed DEPTH report for the chosen idea and persist it. Runs
        ONCE per generate stage (before the revision loop), so all attempts share it. Best-effort —
        research is enrichment, so any failure degrades to no research rather than blocking the run. On
        a resume it reuses a prior ``research.json`` for the SAME idea (research is a slow LLM + web
        call) unless ``force``."""
        if not self.s.research_enabled:
            return None
        if not force and paths.research.exists():
            try:
                cached = load_model(ResearchBrief, paths.research, expected_stage="research")
            except SchemaValidationError:
                cached = None
            if cached and cached.idea == idea:
                self._emit("done", label="Research (reused cached)",
                           detail=f"{len(cached.points)} points")
                return cached
        self._emit("step", label="Researching the topic")
        try:
            research = Researcher(self.s, self._llm_provider()).run(run_id, brief, idea=idea)
        except Exception as exc:  # never fatal
            self.log.warning("research_failed", run_id=run_id, error=str(exc))
            return None
        save_model(research, paths.research)
        if research.used_model:
            self.credit.record(research.used_model, 3000, 400)
        self._emit("done", label="Research", detail=f"{len(research.points)} points")
        return research

    def _augment_brief(self, brief: DataBrief, research: ResearchBrief | None) -> DataBrief:
        """Fold the Researcher's idea-relevant findings into the brief's CITABLE key_facts (prepended,
        so they rank first). This grounds the script in on-topic, source-backed specifics instead of
        letting it drift to whatever numbers happen to be in the raw feed. Used for BOTH generation
        and judging so their fact_refs line up."""
        if not research or not research.points:
            return brief
        return brief.model_copy(
            update={"key_facts": research_key_facts(research) + list(brief.key_facts)}
        )

    def _load_research(self, paths) -> ResearchBrief | None:
        """Best-effort load of a run's saved research.json — so a `--from-stage judge` resume can
        re-augment the brief exactly as generation did (else research-cited fact_refs fall out of
        range and those stats silently score as ungrounded). None if absent/unreadable."""
        if not paths.research.exists():
            return None
        try:
            return load_model(ResearchBrief, paths.research, expected_stage="research")
        except SchemaValidationError:
            return None

    def _gen_judge_loop(
        self, run_id, paths, produced, hashes, *, template_id, idea=None, force=False
    ) -> Verdict | None:
        brief = self._need(produced, "data_brief", paths)
        # Exclude THIS run's own rows: template/hook fatigue is a CROSS-video signal, so iterating or
        # re-judging a single run must not fail structural-freshness against its own prior attempts.
        recent_ids = self.repo.recent_template_ids(self.s.fatigue_lookback, exclude_run_id=run_id)
        recent_hooks = self.repo.recent_hooks(self.s.fatigue_lookback, exclude_run_id=run_id)
        template = get_template(template_id) if template_id else select_template(recent_ids)
        idea = self._resolve_idea(run_id, paths, brief, idea, recent_hooks, force=force)
        research = self._run_research(run_id, paths, brief, idea, force=force)
        gen_brief = self._augment_brief(brief, research)

        feedback: str | None = None
        perspective = ""
        forced = False
        verdict: Verdict | None = None
        previous_script: Script | None = None
        # Track the BEST-scoring draft so far: each revision iterates from it (not the last attempt),
        # so one bad revision can't drag the loop away from a near-miss.
        best_total, best_script, best_feedback = -1.0, None, None
        # Resume-safe: continue the DB attempt numbering past any attempts from a prior run so the
        # UNIQUE(run_id, attempt_number) key never collides — while the judge still counts THIS
        # session's attempts from 1, so a resume gets a fresh MAX_REVISIONS budget.
        prior = self.repo.get_attempts(run_id)
        db_offset = prior[-1].attempt_number if prior else 0

        for attempt_number in range(1, self.s.max_revisions + 1):
            self._check_budget(run_id)
            self._emit("step",
                       label=f"Generating script (attempt {attempt_number}/{self.s.max_revisions})")
            attempt_id = str(ULID())
            script = ScriptGenerator(self.s, self._llm_provider()).run(
                run_id, gen_brief, template,
                perspective_modifier=perspective, judge_feedback=feedback,
                attempt_number=attempt_number, idea=idea, previous_script=previous_script,
                research=research,
            )
            self.repo.add_attempt(attempt_id, run_id, db_offset + attempt_number, template.id, forced)
            self._persist(
                run_id, "script", script, paths, produced, hashes, attempt_id,
                {"data_brief": hashes.get("data_brief", "")},
            )
            self.repo.record_template_usage(run_id, template.id, script.hook)
            self.repo.update_run(run_id, state=RunState.GENERATED.value)
            self._estimate_generate_spend(script)

            self._emit("step",
                       label=f"Evaluating script (attempt {attempt_number}/{self.s.max_revisions})")
            report = Judge(self.s, self._llm_provider()).run(
                run_id, script, gen_brief, attempt_number=attempt_number,
                recent_template_ids=recent_ids, recent_hooks=recent_hooks,
            )
            self._record_judge_attempt(run_id, attempt_id, report, paths, produced, hashes)
            self.repo.update_run(
                run_id, state=RunState.JUDGED.value, final_verdict=report.verdict.value
            )
            if report.provenance.model:
                self.credit.record(self.s.judge_model, 800, 120)
            verdict = report.verdict
            self._emit(
                "judge", n=attempt_number, verdict=report.verdict.value,
                total=report.weighted_total, insight=report.insight_score,
                reason=self._revise_reason(report, script),
            )

            if report.verdict == Verdict.PASS:
                self.repo.update_run(
                    run_id, state=RunState.APPROVED.value, approved_attempt_id=attempt_id
                )
                break
            if report.verdict == Verdict.FAIL:
                self.repo.update_run(run_id, state=RunState.FAILED.value)
                break

            # Opt-in fail-fast: stop paying for revisions a hopeless script can't recover from.
            if (
                self.s.fail_fast_score > 0
                and attempt_number >= 2
                and report.weighted_total < self.s.fail_fast_score
            ):
                self.log.warning(
                    "fail_fast_abort", run_id=run_id,
                    weighted_total=report.weighted_total, threshold=self.s.fail_fast_score,
                )
                self.repo.update_run(
                    run_id, state=RunState.FAILED.value, final_verdict=Verdict.FAIL.value
                )
                verdict = Verdict.FAIL
                break

            if report.force_shift and report.forced_template_id:
                # Structural shift: regenerate fresh on the new template (the old best no longer fits).
                template = get_template(report.forced_template_id)
                perspective = pick_perspective_modifier(random.Random(attempt_number))
                forced = True
                previous_script, feedback = None, report.revision_instructions
                best_total, best_script, best_feedback = -1.0, None, None
            else:
                # Anchor the next revision on the BEST draft so far (not the last one), so a revision
                # that regresses wittiness/ending can't derail the loop off a near-miss.
                if report.weighted_total > best_total:
                    best_total = report.weighted_total
                    best_script = script
                    best_feedback = report.revision_instructions
                previous_script, feedback = best_script, best_feedback
            self.repo.update_run(run_id, state=RunState.REVISING.value)

        return verdict

    def _generate_once(
        self, run_id, paths, produced, hashes, *, template_id, idea=None, force=False
    ) -> None:
        brief = self._need(produced, "data_brief", paths)
        # Exclude THIS run's own rows so a re-generate on one run_id doesn't count against itself.
        recent_ids = self.repo.recent_template_ids(self.s.fatigue_lookback, exclude_run_id=run_id)
        template = get_template(template_id) if template_id else select_template(recent_ids)
        idea = self._resolve_idea(
            run_id, paths, brief, idea,
            self.repo.recent_hooks(self.s.fatigue_lookback, exclude_run_id=run_id), force=force,
        )
        research = self._run_research(run_id, paths, brief, idea, force=force)
        gen_brief = self._augment_brief(brief, research)
        attempt_id = str(ULID())
        script = ScriptGenerator(self.s, self._llm_provider()).run(
            run_id, gen_brief, template, attempt_number=1, idea=idea, research=research
        )
        self.repo.add_attempt(attempt_id, run_id, 1, template.id, False)
        self._persist(
            run_id, "script", script, paths, produced, hashes, attempt_id,
            {"data_brief": hashes.get("data_brief", "")},
        )
        self.repo.record_template_usage(run_id, template.id, script.hook)
        self.repo.update_run(run_id, state=RunState.GENERATED.value)
        self._estimate_generate_spend(script)

    def _judge_once(self, run_id, paths, produced, hashes) -> Verdict:
        script = self._need(produced, "script", paths)
        brief = self._need(produced, "data_brief", paths)
        # Re-augment with the saved research so the script's research-derived fact_refs line up with
        # the brief the Judge grounds against (generation used the augmented brief). Without this a
        # `--from-stage judge` resume silently scores those research-cited stats as ungrounded.
        brief = self._augment_brief(brief, self._load_research(paths))
        # Exclude THIS run's own rows so re-judging one run doesn't fail freshness against itself.
        recent_ids = self.repo.recent_template_ids(self.s.fatigue_lookback, exclude_run_id=run_id)
        recent_hooks = self.repo.recent_hooks(self.s.fatigue_lookback, exclude_run_id=run_id)
        attempts = self.repo.get_attempts(run_id)
        n = (attempts[-1].attempt_number + 1) if attempts else 1
        attempt_id = str(ULID())
        self.repo.add_attempt(attempt_id, run_id, n, script.template_id, False)
        report = Judge(self.s, self._llm_provider()).run(
            run_id, script, brief, attempt_number=n,
            recent_template_ids=recent_ids, recent_hooks=recent_hooks,
        )
        self._record_judge_attempt(run_id, attempt_id, report, paths, produced, hashes)
        state = RunState.APPROVED if report.verdict == Verdict.PASS else (
            RunState.FAILED if report.verdict == Verdict.FAIL else RunState.JUDGED
        )
        fields = {"state": state.value, "final_verdict": report.verdict.value}
        if report.verdict == Verdict.PASS:
            fields["approved_attempt_id"] = attempt_id
        self.repo.update_run(run_id, **fields)
        return report.verdict

    def _direct_broll(self, script):
        """LLM visual-director pass (Agent 5.5): rewrite each scene's B-roll queries for relevance +
        cross-scene diversity. Gated + best-effort; keeps the generator's keywords on any failure."""
        if not self.s.broll_director_enabled or not script.scenes:
            return script
        try:
            from ..agents.broll_director import BrollDirector

            return BrollDirector(self.s, self._llm_provider()).run(script)
        except Exception as exc:  # never let visual direction break the visuals stage
            self.log.warning("broll_director_skipped", error=str(exc))
            return script

    # --------------------------------------------------------- production
    def _run_production_stage(
        self, stage, run_id, run_root, paths, produced, hashes, *, force=False
    ) -> None:
        stage_key = {"voiceover": "voiceover", "visuals": "visuals",
                     "render": "video", "publish": "publish"}[stage]
        label = _STAGE_LABELS[stage]
        if not force and stage_key in produced and paths.artifact(stage_key).exists():
            self.log.info("stage_skipped_cached", run_id=run_id, stage=stage)
            self._emit("done", label=f"{label} (reused cached)")
            return
        if stage in ("voiceover", "visuals"):
            self._check_budget(run_id)
        self._emit("step", label=label)
        if stage == "voiceover":
            script = self._need(produced, "script", paths)
            vo = Voiceover(self.s, self._tts_provider(run_id)).run(run_id, script, run_root=run_root)
            self._persist(run_id, "voiceover", vo, paths, produced, hashes, None,
                          {"script": hashes.get("script", "")})
            self.repo.update_run(run_id, state=RunState.VOICED.value)

        elif stage == "visuals":
            script = self._need(produced, "script", paths)
            script = self._direct_broll(script)
            vo = self._need(produced, "voiceover", paths)
            vis = Visuals(self.s, self._image_provider(), self._broll_client()).run(
                run_id, script, vo, run_root=run_root
            )
            self._persist(run_id, "visuals", vis, paths, produced, hashes, None,
                          {"voiceover": hashes.get("voiceover", "")})
            self.repo.update_run(run_id, state=RunState.VISUALIZED.value)

        elif stage == "render":
            vo = self._need(produced, "voiceover", paths)
            vis = self._need(produced, "visuals", paths)
            video = Renderer(self.s, self._render_backend(), self._sfx_client()).run(
                run_id, vo, vis, run_root=run_root
            )
            self._persist(run_id, "video", video, paths, produced, hashes, None,
                          {"visuals": hashes.get("visuals", "")})
            self.repo.update_run(run_id, state=RunState.RENDERED.value)

        elif stage == "publish":
            video = self._need(produced, "video", paths)
            script = self._need(produced, "script", paths)
            vis = self._need(produced, "visuals", paths)
            pub = Publisher(self.s, self._publisher_obj()).run(
                run_id, video, script, vis, run_root=run_root
            )
            self._persist(run_id, "publish", pub, paths, produced, hashes, None,
                          {"video": hashes.get("video", "")})
            self.repo.add_publish_result(
                run_id=run_id, attempt_id=None,
                youtube_video_id=pub.youtube_video_id, video_url=pub.video_url,
                privacy_status=pub.privacy_status, disclosure_set=pub.disclosure_set,
                upload_status=pub.upload_status,
                published_at=pub.published_at.isoformat() if pub.published_at else None,
            )
            self.repo.update_run(run_id, state=RunState.PUBLISHED.value)
            self._publish_notifications(run_id, pub)

        self._emit("done", label=label)

    def _publish_notifications(self, run_id, pub: PublishResult) -> None:
        if pub.upload_status in ("uploaded", "pending_manual_disclosure"):
            self.notifier.send(
                "video_uploaded", "📤 Video uploaded",
                f"{run_id}: {pub.video_url} ({pub.privacy_status})", meta={"run_id": run_id},
            )
        if pub.privacy_status != "public" or pub.upload_status == "pending_manual_disclosure":
            self.notifier.send(
                "need_validation", "🔎 Needs your go-live OK",
                f"{run_id}: draft awaiting approval ({pub.upload_status})", meta={"run_id": run_id},
            )

    def _check_budget(self, run_id: str) -> None:
        """Hard budget cap (cost safety): abort before more spend once over the monthly budget."""
        if self.s.enforce_budget_cap and self.credit.over_budget:
            self.log.error(
                "budget_exhausted", run_id=run_id,
                spend=round(self.credit.month_to_date_usd, 4), budget=self.s.monthly_budget_usd,
            )
            raise BudgetExhaustedError(
                f"Estimated month-to-date spend ${self.credit.month_to_date_usd:.2f} reached the "
                f"${self.s.monthly_budget_usd:.2f} cap (set ENFORCE_BUDGET_CAP=false to disable)."
            )

    def _record_judge_attempt(self, run_id, attempt_id, report, paths, produced, hashes) -> None:
        """Persist a judge report + rubric scores + the attempt verdict (shared by the two judge paths)."""
        self._persist(
            run_id, "judge_report", report, paths, produced, hashes, attempt_id,
            {"script": hashes.get("script", "")},
        )
        self.repo.add_rubric_scores(
            attempt_id,
            [
                {"dimension": d.dimension, "score": d.score, "weight": d.weight,
                 "passed": d.passed, "comment": d.justification}
                for d in report.scores
            ],
        )
        self.repo.update_attempt(
            attempt_id, verdict=report.verdict.value,
            insight_score=report.insight_score, weighted_total=report.weighted_total,
        )

    # ============================================================= persistence
    def _persist(self, run_id, key, model, paths, produced, hashes, attempt_id, input_hashes):
        model.provenance.input_hashes = input_hashes or {}
        path = paths.artifact(key)
        save_model(model, path)
        digest = sha256_file(path)
        produced[key] = model
        hashes[key] = digest
        self.repo.add_artifact(
            artifact_id=str(ULID()), run_id=run_id, attempt_id=attempt_id, stage=key,
            schema_version=model.schema_version, path=str(path), content_hash=digest,
            provenance=model.provenance.model_dump(mode="json"),
        )

    def _preload(self, run_id, from_stage, input_path, paths, produced, hashes) -> None:
        if input_path:
            self._load_external(run_id, input_path, paths, produced, hashes)
        if from_stage == "fetch":
            return
        for key in ARTIFACT_MODELS:
            if key in produced:
                continue
            path = paths.artifact(key)
            if path.exists():
                self._load_with_edit_detection(run_id, key, path, produced, hashes)

    def _load_external(self, run_id, input_path, paths, produced, hashes) -> None:
        import json

        raw = json.loads(Path(input_path).read_text(encoding="utf-8"))
        key = raw.get("stage")
        if key not in ARTIFACT_MODELS:
            raise SchemaValidationError(f"{input_path}: unknown artifact stage {key!r}")
        model_cls, expected = ARTIFACT_MODELS[key]
        model = load_model(model_cls, input_path, expected_stage=expected)
        model.provenance.produced_by = "operator_edited"
        self._persist(run_id, key, model, paths, produced, hashes, None,
                      model.provenance.input_hashes)

    def _load_with_edit_detection(self, run_id, key, path, produced, hashes) -> None:
        model_cls, expected = ARTIFACT_MODELS[key]
        model = load_model(model_cls, path, expected_stage=expected)
        digest = sha256_file(path)
        recorded = self.repo.latest_artifact(run_id, key)
        if recorded and recorded.content_hash != digest:
            self.log.info("operator_edit_detected", run_id=run_id, stage=key)
            model.provenance.produced_by = "operator_edited"
            save_model(model, path)
            digest = sha256_file(path)
            self.repo.add_artifact(
                artifact_id=str(ULID()), run_id=run_id, attempt_id=None, stage=key,
                schema_version=model.schema_version, path=str(path), content_hash=digest,
                provenance=model.provenance.model_dump(mode="json"),
            )
        produced[key] = model
        hashes[key] = digest

    # ============================================================== helpers
    def _need(self, produced, key, paths):
        if key in produced:
            return produced[key]
        path = paths.artifact(key)
        if not path.exists():
            raise ContentFoundryError(f"Required artifact '{key}' is missing at {path}")
        model_cls, expected = ARTIFACT_MODELS[key]
        model = load_model(model_cls, path, expected_stage=expected)
        produced[key] = model
        return model

    def _latest_verdict(self, run_id, produced) -> Verdict | None:
        jr = produced.get("judge_report")
        if isinstance(jr, JudgeReport):
            return jr.verdict
        path = run_paths(run_id, self.s.output_dir).artifact("judge_report")
        if path.exists():
            try:
                return load_model(JudgeReport, path, expected_stage="judge_report").verdict
            except SchemaValidationError:
                pass
        run = self.repo.get_run(run_id)
        if run and run.final_verdict:
            return Verdict(run.final_verdict)
        return None

    def _assemble_package(self, run_id, paths, produced) -> None:
        script = produced.get("script")
        if not isinstance(script, Script):
            if paths.artifact("script").exists():
                script = load_model(Script, paths.artifact("script"), expected_stage="script")
            else:
                return
        md = build_package_md(
            run_id,
            script=script,
            judge_report=produced.get("judge_report"),
            publish_result=produced.get("publish"),
            brief=produced.get("data_brief"),
            visuals=produced.get("visuals"),
            ideas=self._load_ideas(paths),
        )
        paths.package.write_text(md, encoding="utf-8")

    def _load_ideas(self, paths) -> IdeaSelection | None:
        """Best-effort read of the ideas.json sidecar for the package summary (never fatal)."""
        if not paths.ideas.exists():
            return None
        try:
            return load_model(IdeaSelection, paths.ideas, expected_stage="ideas")
        except SchemaValidationError:
            return None

    def _build_result(self, run_id, stages, paths, produced) -> RunResult:
        run = self.repo.get_run(run_id)
        final_state = RunState(run.state) if run else RunState.CREATED
        verdict = self._latest_verdict(run_id, produced)
        artifacts = {
            key: str(paths.artifact(key)) for key in produced if key in ARTIFACT_FILENAMES
        }
        pub = produced.get("publish")
        return RunResult(
            run_id=run_id,
            final_state=final_state,
            verdict=verdict,
            from_stage=stages[0],
            to_stage=stages[-1],
            artifacts=artifacts,
            video_url=pub.video_url if isinstance(pub, PublishResult) else None,
            package_path=str(paths.package) if paths.package.exists() else None,
        )

    def _estimate_generate_spend(self, script: Script) -> None:
        est_completion = int(script.word_count * 1.3) + 200
        self.credit.record(self.s.generator_model, 1500, est_completion)

    def _persist_spend(self) -> None:
        self.repo.set_meta("spend_month", str(self.credit.month_to_date_usd))

    def _ensure_run(self, run_id, topic_seed) -> str:
        if run_id is None:
            run_id = next_run_id(self.s.output_dir)
            # Never reuse an id still in the DB (e.g. its output folder was deleted).
            while self.repo.get_run(run_id) is not None:
                run_id = f"{int(run_id) + 1:04d}"
            self.repo.create_run(run_id, topic_seed, RunState.CREATED.value)
        elif self.repo.get_run(run_id) is None:
            self.repo.create_run(run_id, topic_seed, RunState.CREATED.value)
        return run_id

    def _build_repo(self) -> Repository:
        engine = make_engine(self.s.database_url)
        init_db(engine)
        return Repository(make_session_factory(engine))

    # --------------------------------------------------- lazy provider getters
    def _llm_provider(self):
        if self._llm is None:
            self._llm = build_llm_provider(self.s)
        return self._llm

    def _tts_provider(self, run_id=None):
        if self._tts is not None:
            return self._tts  # injected (tests) or already built
        return build_tts_provider(self.s, run_id=run_id)

    def _image_provider(self):
        if self._image is _UNSET:
            self._image = build_image_provider(self.s)
        return self._image

    def _broll_client(self):
        if self._broll is None:
            self._broll = build_broll_client(self.s)
        return self._broll

    def _render_backend(self):
        if self._render is None:
            self._render = build_render_backend(self.s)
        return self._render

    def _sfx_client(self):
        if self._sfx is None:
            self._sfx = build_sfx_client(self.s)
        return self._sfx

    def _publisher_obj(self):
        if self._publisher is None:
            self._publisher = build_publisher(self.s, dry_run=self.dry_run)
        return self._publisher


def run_pipeline(
    *,
    run_id: str | None = None,
    from_stage: str = "fetch",
    to_stage: str = "publish",
    input_path: str | None = None,
    template_id: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    niche: str | None = None,
    topic_seed: str | None = None,
    idea: str | None = None,
    idea_chooser=None,
    orchestrator: Orchestrator | None = None,
    reporter=None,
) -> RunResult:
    """Thin functional entry point over :class:`Orchestrator` (Ch. 14.3)."""
    orch = orchestrator or Orchestrator(dry_run=dry_run, reporter=reporter, idea_chooser=idea_chooser)
    return orch.run(
        run_id=run_id, from_stage=from_stage, to_stage=to_stage, input_path=input_path,
        template_id=template_id, force=force, niche=niche, topic_seed=topic_seed, idea=idea,
    )
