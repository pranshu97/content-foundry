"""APScheduler entry point — unattended, draft-only runs on a cron (Ch. 18)."""

from __future__ import annotations

from .config import get_settings
from .logging import configure_logging, get_logger
from .pipeline.orchestrator import run_pipeline

_log = get_logger(component="scheduler")
_running = False


def _fire(to_stage: str = "publish") -> None:
    """One scheduled run, guarded by a single-flight lock; failures never crash the loop."""
    global _running
    if _running:
        _log.warning("scheduler_skip_overlap")
        return
    _running = True
    try:
        result = run_pipeline(from_stage="fetch", to_stage=to_stage)
        _log.info("scheduled_run_done", run_id=result.run_id, state=result.final_state.value)
    except Exception as exc:  # never crash the scheduler
        _log.error("scheduled_run_failed", error=str(exc))
    finally:
        _running = False


def start(cron: str | None = None, to_stage: str = "publish") -> None:  # pragma: no cover
    """Block on an APScheduler loop using ``SCHEDULE_CRON`` (or an override)."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    configure_logging()
    settings = get_settings()
    cron = cron or settings.schedule_cron
    scheduler = BlockingScheduler()
    scheduler.add_job(_fire, CronTrigger.from_crontab(cron), kwargs={"to_stage": to_stage})
    _log.info("scheduler_start", cron=cron)
    scheduler.start()
