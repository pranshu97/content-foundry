"""Typer CLI exposed as ``career`` — thin wrappers over the orchestrator (Ch. 17)."""

from __future__ import annotations

import os

import typer
from rich.console import Console
from rich.table import Table

from .config import PROFILES, get_settings, reset_settings_cache
from .errors import CareerEngineError
from .logging import configure_logging
from .persistence import Repository, init_db, make_engine, make_session_factory

app = typer.Typer(add_completion=False, help="Content Foundry CLI.")
config_app = typer.Typer(help="Configuration utilities.")
app.add_typer(config_app, name="config")
console = Console()

_STATE = {"dry_run": False}

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


def _repo() -> Repository:
    engine = make_engine(get_settings().database_url)
    init_db(engine)
    return Repository(make_session_factory(engine))


def _run(**kwargs):
    from .pipeline.orchestrator import run_pipeline

    kwargs.setdefault("dry_run", _STATE["dry_run"])
    try:
        result = run_pipeline(**kwargs)
    except CareerEngineError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    color = {"PASS": "green", "REVISE": "yellow", "FAIL": "red"}.get(
        result.verdict.value if result.verdict else "", "cyan"
    )
    console.print(
        f"[bold]run_id:[/bold] {result.run_id}  "
        f"[bold]state:[/bold] {result.final_state.value}  "
        f"[{color}]verdict: {result.verdict.value if result.verdict else 'n/a'}[/{color}]"
    )
    for stage, path in result.artifacts.items():
        console.print(f"  {stage}: {path}")
    if result.video_url:
        console.print(f"  youtube: {result.video_url}")
    return result


@app.command()
def run(
    niche: str | None = typer.Option(None),
    topic: str | None = typer.Option(None),
    template: str | None = typer.Option(None),
    from_stage: str = typer.Option("fetch", "--from-stage"),
    to_stage: str = typer.Option("publish", "--to-stage"),
    input: str | None = typer.Option(None),
    run_id: str | None = typer.Option(None, "--run-id"),
    force: bool = typer.Option(False),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Run the pipeline end-to-end (or a slice via --from-stage/--to-stage)."""
    _run(
        run_id=run_id, from_stage=from_stage, to_stage=to_stage, input_path=input,
        template_id=template, force=force, dry_run=dry_run or _STATE["dry_run"],
        niche=niche, topic_seed=topic,
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
) -> None:
    """Continue a run from its next stage."""
    run = _repo().get_run(run_id)
    if run is None:
        console.print(f"[red]No such run:[/red] {run_id}")
        raise typer.Exit(code=1)
    next_stage = _NEXT_STAGE.get(run.state, "fetch")
    console.print(f"Resuming {run_id} from [cyan]{next_stage}[/cyan] (state={run.state})")
    _run(run_id=run_id, from_stage=next_stage, to_stage=to_stage, force=True)


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
    except CareerEngineError as exc:
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
