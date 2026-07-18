"""Typer CLI exposed as ``content-foundry`` — thin wrappers over the orchestrator (Ch. 17)."""

from __future__ import annotations

import contextlib
import os
import sys

import typer
from rich.console import Console
from rich.table import Table

from .config import PROFILES, get_settings, reset_settings_cache
from .errors import ContentFoundryError
from .logging import configure_logging
from .persistence import Repository, init_db, make_engine, make_session_factory

# Windows consoles / redirected pipes default to cp1252 and crash on the ✓/✗/emoji we print;
# force UTF-8 so `config check`, logs, and run output never raise UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):  # non-reconfigurable stream
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

app = typer.Typer(add_completion=False, help="Content Foundry CLI.")
config_app = typer.Typer(help="Configuration utilities.")
app.add_typer(config_app, name="config")
console = Console()

_STATE = {"dry_run": False, "format_explicit": False}

_NEXT_STAGE = {
    "CREATED": "fetch",
    "FETCHED": "generate",
    "GENERATED": "judge",
    "REVISING": "generate",
    "JUDGED": "voiceover",
    "APPROVED": "voiceover",
    "VOICED": "visuals",
    "VISUALIZED": "render",
    "RENDERED": "publish",
}


@app.callback()
def main(
    profile: str | None = typer.Option(None, help="cheap | quality"),
    log_level: str | None = typer.Option(None, "--log-level"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    if profile:
        if profile not in PROFILES:
            raise typer.BadParameter(f"Unknown profile {profile!r}; choose from {list(PROFILES)}")
        for key, value in PROFILES[profile].items():
            os.environ[key.upper()] = str(value)
        reset_settings_cache()
    if log_level:
        os.environ["LOG_LEVEL"] = log_level
        reset_settings_cache()
    _STATE["dry_run"] = dry_run
    configure_logging()


class _RunReporter:
    """Clean, live pipeline progress for interactive runs: a spinner per step + judge scores."""

    def __init__(self, con: Console) -> None:
        self._c = con
        self._status = None

    def close(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def __call__(self, event: str, **d: object) -> None:
        if event == "start":
            self._c.print(
                f"\n[bold cyan]▶ content-foundry[/]  [dim]niche[/] {d.get('niche')}  "
                f"[dim]·[/]  {d.get('from_stage')} → {d.get('to_stage')}"
            )
        elif event == "step":
            self.close()
            self._status = self._c.status(f"[cyan]{d['label']}…[/]", spinner="dots")
            self._status.start()
        elif event == "done":
            self.close()
            detail = f"  [dim]— {d['detail']}[/]" if d.get("detail") else ""
            self._c.print(f"  [green]✓[/] {d['label']}{detail}")
        elif event == "judge":
            self.close()
            verdict = str(d.get("verdict"))
            color = {"PASS": "green", "REVISE": "yellow", "FAIL": "red"}.get(verdict, "white")
            reason = d.get("reason")
            reason_txt = f"  [yellow]· {reason}[/]" if reason and verdict != "PASS" else ""
            self._c.print(
                f"  [{color}]⚖ attempt {d['n']} → {verdict}[/]  [dim]score[/] "
                f"[bold]{float(d['total']):.2f}[/][dim]/5 · insight[/] "
                f"{float(d['insight']):.1f}{reason_txt}"
            )
        elif event == "gate":
            self.close()
            if d.get("ok"):
                self._c.print("  [green]✓ production gate passed[/]")
            elif d.get("awaiting_approval"):
                self._c.print(
                    "  [yellow]⏸ script approved by the reviewer — awaiting your sign-off[/]  "
                    f"[dim]review script.json, then:[/] content-foundry resume --run-id {d.get('run_id')}"
                )
            else:
                self._c.print(
                    f"  [red]⛔ blocked at production gate[/] "
                    f"[dim](needs a PASS — got {d.get('verdict')})[/]"
                )
        elif event == "end":
            self.close()


def _repo() -> Repository:
    engine = make_engine(get_settings().database_url)
    init_db(engine)
    return Repository(make_session_factory(engine))


def _infer_next_stage(run_id: str) -> str:
    """Fallback resume point for states without a clean 'next' (e.g. FAILED): resume from the first
    missing stage based on which artifacts exist on disk — a FAILED script retries from 'generate',
    a render that died resumes from 'render', etc."""
    from .pipeline.artifacts import run_paths

    paths = run_paths(run_id, get_settings().output_dir)
    for artifact, nxt in (
        ("video", "publish"),
        ("visuals", "render"),
        ("voiceover", "visuals"),
        ("data_brief", "generate"),
    ):
        if paths.artifact(artifact).exists():
            return nxt
    return "fetch"


def _make_idea_chooser(reporter: _RunReporter):
    """An interactive picker: show the brainstormed ideas and let the operator pick one — or choose
    0 to type their own idea instead."""
    def _choose(ideas: list[str]) -> str:
        reporter.close()  # stop the spinner before prompting
        console.print("\n[bold]Pick a video idea:[/]")
        for i, idea in enumerate(ideas, 1):
            console.print(f"  [cyan]{i}[/]. {idea}")
        console.print("  [cyan]0[/]. [italic]Enter my own idea…[/]")
        try:
            n = typer.prompt("Your choice", type=int, default=1)
        except (typer.Abort, EOFError):
            return ideas[0]
        if n == 0:  # operator types a bespoke idea instead of picking a proposal
            try:
                custom = typer.prompt("Your idea").strip()
            except (typer.Abort, EOFError):
                return ideas[0]
            return custom or ideas[0]
        return ideas[n - 1] if 1 <= n <= len(ideas) else ideas[0]

    return _choose


def _apply_run_format(run_id: str | None) -> None:
    """Pin CONTENT_FORMAT to an EXISTING run's own format so a re-run / thumbnail refinement stays the
    same shape (a Short stays a Short) regardless of the .env default. An explicit --format this
    invocation wins; a brand-new run is unaffected. Older runs (no run_meta.json) infer from the
    rendered resolution."""
    if not run_id or _STATE.get("format_explicit"):
        return
    from .pipeline.artifacts import load_run_format

    fmt = load_run_format(run_id, get_settings().output_dir)
    if fmt and fmt != get_settings().content_format:
        os.environ["CONTENT_FORMAT"] = fmt
        reset_settings_cache()


def _run(**kwargs):
    from .pipeline.orchestrator import run_pipeline

    _apply_run_format(kwargs.get("run_id"))
    kwargs.setdefault("dry_run", _STATE["dry_run"])
    reporter = _RunReporter(console)
    if kwargs.pop("interactive_idea", False) and sys.stdin.isatty():
        kwargs["idea_chooser"] = _make_idea_chooser(reporter)
    try:
        result = run_pipeline(reporter=reporter, **kwargs)
    except ContentFoundryError as exc:
        reporter.close()
        console.print(f"[red]✗ Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:  # Ctrl+C is honored mid-LLM-call via providers.base.run_interruptible
        reporter.close()
        console.print("\n[yellow]✗ Cancelled.[/yellow]")
        raise typer.Exit(code=130) from None
    reporter.close()

    color = {"PASS": "green", "REVISE": "yellow", "FAIL": "red"}.get(
        result.verdict.value if result.verdict else "", "cyan"
    )
    console.print()
    console.print(
        f"[bold]run_id[/] {result.run_id}   "
        f"[bold]state[/] [{color}]{result.final_state.value}[/]   "
        f"[bold]verdict[/] [{color}]{result.verdict.value if result.verdict else 'n/a'}[/]"
    )
    for stage, path in result.artifacts.items():
        console.print(f"  [dim]{stage:<12}[/] {path}")
    if result.video_url:
        console.print(f"  [bold]youtube[/]      {result.video_url}")
    return result


@app.command()
def run(
    niche: str | None = typer.Option(None),
    topic: str | None = typer.Option(None),
    idea: str | None = typer.Option(None, "--idea", help="Focus the brainstormer on your concept (it proposes angles you pick from)"),
    template: str | None = typer.Option(None),
    fmt: str | None = typer.Option(None, "--format", help="long | short (a vertical YouTube Short); overrides CONTENT_FORMAT"),
    from_stage: str = typer.Option("fetch", "--from-stage"),
    to_stage: str = typer.Option("render", "--to-stage", help="Last stage to run; defaults to 'render' (a finished, UNPUBLISHED video)"),
    publish: bool = typer.Option(False, "--publish", help="Also publish to YouTube after rendering (off by default; the run otherwise stops at a finished video — publish later with 'content-foundry publish --run-id ...')"),
    input: str | None = typer.Option(None),
    run_id: str | None = typer.Option(None, "--run-id"),
    force: bool = typer.Option(False),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Run the pipeline end-to-end (or a slice via --from-stage/--to-stage). By default it stops at a
    finished, UNPUBLISHED video; pass --publish (or run `content-foundry publish` later) to upload."""
    if fmt:
        if fmt not in ("long", "short"):
            raise typer.BadParameter("--format must be 'long' or 'short'")
        os.environ["CONTENT_FORMAT"] = fmt
        _STATE["format_explicit"] = True  # an explicit --format wins over the run's persisted format
        reset_settings_cache()
    if publish:
        to_stage = "publish"
    _run(
        run_id=run_id, from_stage=from_stage, to_stage=to_stage, input_path=input,
        template_id=template, force=force, dry_run=dry_run or _STATE["dry_run"],
        niche=niche, topic_seed=topic, idea=idea, interactive_idea=True,
    )


@app.command()
def fetch(
    niche: str | None = typer.Option(None),
    topic: str | None = typer.Option(None),
    run_id: str | None = typer.Option(None, "--run-id"),
) -> None:
    """Stage 1 only → data_brief.json."""
    _run(run_id=run_id, from_stage="fetch", to_stage="fetch", niche=niche, topic_seed=topic)


@app.command()
def generate(
    input: str = typer.Option(..., help="data_brief.json"),
    template: str | None = typer.Option(None),
    run_id: str | None = typer.Option(None, "--run-id"),
) -> None:
    """Stage 2 only (needs a brief)."""
    _run(run_id=run_id, from_stage="generate", to_stage="generate", input_path=input,
         template_id=template)


@app.command()
def judge(
    input: str = typer.Option(..., help="script.json"),
    run_id: str | None = typer.Option(None, "--run-id"),
) -> None:
    """Stage 3 only (needs a script)."""
    _run(run_id=run_id, from_stage="judge", to_stage="judge", input_path=input)


@app.command()
def voiceover(
    input: str | None = typer.Option(None, help="script.json"),
    run_id: str | None = typer.Option(None, "--run-id"),
) -> None:
    """Stage 4 (needs an approved script)."""
    _run(run_id=run_id, from_stage="voiceover", to_stage="voiceover", input_path=input, force=True)


@app.command()
def visuals(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Stage 5."""
    _run(run_id=run_id, from_stage="visuals", to_stage="visuals", force=True)


@app.command()
def thumbnail(
    run_id: str = typer.Option(..., "--run-id"),
    face_id: bool | None = typer.Option(
        None, "--face-id/--no-face-id", help="Override THUMBNAIL_FACE_ID_ENABLED just for this regen"
    ),
    scale: float | None = typer.Option(
        None, "--scale", help="Override FACEID_SCALE (identity strength 0-1.5; lower = more scene, less face)"
    ),
    prompt: str | None = typer.Option(
        None, "--prompt", help="Use this EXACT image prompt (overrides the saved/auto prompt) and save it"
    ),
    reset: bool = typer.Option(
        False, "--reset", help="Rebuild the prompt from the script's thumbnail_concept, discarding saved edits"
    ),
) -> None:
    """Regenerate ONLY the thumbnail for a run — fast iteration without re-running the whole visuals
    stage. The exact image prompt is saved to assets/thumbnail_prompt.txt: EDIT that file and re-run
    this to control the thumbnail, pass --prompt to override it directly, or --reset to rebuild it
    from the script's thumbnail_concept."""
    from .agents.visuals import Visuals
    from .models import Script
    from .pipeline.artifacts import run_paths
    from .providers import build_image_provider, build_llm_provider

    if face_id is not None:
        os.environ["THUMBNAIL_FACE_ID_ENABLED"] = "true" if face_id else "false"
    if scale is not None:
        os.environ["FACEID_SCALE"] = str(scale)
    if face_id is not None or scale is not None:
        reset_settings_cache()

    _apply_run_format(run_id)  # a Short's thumbnail stays vertical on a refinement, without --format
    settings = get_settings()
    paths = run_paths(run_id, settings.output_dir)
    script_path = paths.artifact("script")
    if not script_path.exists():
        raise typer.BadParameter(f"No script.json for run {run_id!r} at {script_path}")
    script = Script.model_validate_json(script_path.read_text(encoding="utf-8"))

    prompt_file = paths.root / "assets" / "thumbnail_prompt.txt"
    if reset and prompt_file.exists():
        prompt_file.unlink()  # discard saved edits -> render rebuilds from the concept
    try:
        llm = build_llm_provider(settings)
    except Exception:
        llm = None  # the thumbnail director is optional; without an LLM the built-in template is used
    Visuals(settings, build_image_provider(settings), None, llm).render_thumbnail(
        script, run_root=paths.root, prompt=prompt,
    )
    typer.echo(f"Thumbnail regenerated: {paths.root / 'assets' / 'thumbnail.png'}")
    typer.echo(f"Edit this to tweak the look, then re-run: {prompt_file}")


@app.command()
def render(
    run_id: str = typer.Option(..., "--run-id"),
    backend: str | None = typer.Option(None),
) -> None:
    """Stage 6."""
    if backend:
        os.environ["RENDER_BACKEND"] = backend
        reset_settings_cache()
    _run(run_id=run_id, from_stage="render", to_stage="render", force=True)


@app.command()
def publish(
    run_id: str = typer.Option(..., "--run-id"),
    privacy: str | None = typer.Option(None),
    mode: str | None = typer.Option(None, help="draft | auto"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Stage 7."""
    if privacy:
        os.environ["YOUTUBE_PRIVACY_STATUS"] = privacy
    if mode:
        os.environ["PUBLISH_MODE"] = mode
    if privacy or mode:
        reset_settings_cache()
    _run(run_id=run_id, from_stage="publish", to_stage="publish", force=True,
         dry_run=dry_run or _STATE["dry_run"])


@app.command()
def resume(
    run_id: str = typer.Option(..., "--run-id"),
    to_stage: str = typer.Option("publish", "--to-stage"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Continue a run from its next stage (auto-detected from the run's state / artifacts)."""
    run = _repo().get_run(run_id)
    if run is None:
        console.print(f"[red]No such run:[/red] {run_id}")
        raise typer.Exit(code=1)
    next_stage = _NEXT_STAGE.get(run.state) or _infer_next_stage(run_id)
    console.print(f"Resuming {run_id} from [cyan]{next_stage}[/cyan] (state={run.state})")
    _run(run_id=run_id, from_stage=next_stage, to_stage=to_stage, force=True,
         dry_run=dry_run or _STATE["dry_run"])


@app.command()
def status(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Show a run's state, attempts, and verdict."""
    repo = _repo()
    run = repo.get_run(run_id)
    if run is None:
        console.print(f"[red]No such run:[/red] {run_id}")
        raise typer.Exit(code=1)
    console.print(f"[bold]{run_id}[/bold] state={run.state} verdict={run.final_verdict}")
    table = Table("attempt", "template", "verdict", "insight", "weighted")
    for att in repo.get_attempts(run_id):
        table.add_row(
            str(att.attempt_number), att.template_id, str(att.verdict),
            str(att.insight_score), str(att.weighted_total),
        )
    console.print(table)


@app.command("list")
def list_runs(
    limit: int = typer.Option(20),
    state: str | None = typer.Option(None),
) -> None:
    """List recent runs."""
    table = Table("run_id", "state", "verdict", "created_at")
    for run in _repo().list_runs(limit=limit, state=state):
        table.add_row(run.run_id, run.state, str(run.final_verdict), run.created_at)
    console.print(table)


@app.command()
def report(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Pretty-print the latest JudgeReport for a run."""
    from .models import JudgeReport
    from .pipeline.artifacts import load_model, run_paths

    path = run_paths(run_id, get_settings().output_dir).artifact("judge_report")
    if not path.exists():
        console.print(f"[red]No judge_report for[/red] {run_id}")
        raise typer.Exit(code=1)
    jr = load_model(JudgeReport, path, expected_stage="judge_report")
    color = {"PASS": "green", "REVISE": "yellow", "FAIL": "red"}.get(jr.verdict.value, "cyan")
    console.print(f"[{color}]{jr.verdict.value}[/{color}] weighted={jr.weighted_total} "
                  f"insight={jr.insight_score} grounding={jr.grounding_score}")
    table = Table("dimension", "score", "weight", "passed")
    for d in jr.scores:
        table.add_row(d.dimension, str(d.score), str(d.weight), "✓" if d.passed else "✗")
    console.print(table)
    console.print(jr.summary)


@app.command()
def dashboard(port: int = typer.Option(8501)) -> None:  # pragma: no cover
    """Launch the Streamlit review dashboard."""
    import subprocess
    import sys
    from pathlib import Path

    app_path = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port)],
        check=False,
    )


@app.command("init-db")
def init_db_cmd() -> None:
    """Create database tables."""
    init_db(make_engine(get_settings().database_url))
    console.print("[green]Database initialised.[/green]")


@config_app.command("check")
def config_check(profile: str | None = typer.Option(None)) -> None:
    """Validate config and print a redacted credential table."""
    if profile and profile in PROFILES:
        for key, value in PROFILES[profile].items():
            os.environ[key.upper()] = str(value)
        reset_settings_cache()
    try:
        settings = get_settings()
    except ContentFoundryError as exc:
        console.print(f"[red]Config invalid:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    table = Table("credential", "status")
    for name, state in settings.credential_status().items():
        table.add_row(name, state)
    console.print(table)
    console.print(f"config_hash: {settings.config_hash}")


@app.command("notify-test")
def notify_test() -> None:
    """Send a sample of each configured NOTIFY_EVENTS alert."""
    from .notifications import build_notifier

    settings = get_settings()
    notifier = build_notifier(settings)
    for event in settings.notify_events_list:
        notifier.send(event, f"[test] {event}", "Sample alert from content-foundry notify-test.")
    console.print(f"[green]Sent {len(settings.notify_events_list)} test alert(s).[/green]")


@app.command()
def schedule(cron: str | None = typer.Option(None)) -> None:  # pragma: no cover
    """Start the APScheduler loop."""
    from .scheduler import start

    start(cron=cron)


if __name__ == "__main__":  # pragma: no cover
    app()
