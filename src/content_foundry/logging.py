"""structlog setup with run-scoped binding (Ch. 21.5)."""

from __future__ import annotations

import contextvars
import json
import logging
from typing import Any

import structlog

_CONFIGURED = False
# Absolute path of the current run's log file; when set, every structured log line is ALSO appended
# there (JSON lines) so a run is fully debuggable after the fact — including silent model fallbacks.
_RUN_LOG_FILE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_log_file", default=None
)


def set_run_log_file(path: str | None) -> None:
    """Tee all subsequent structured logs to ``path`` (a per-run JSON-lines file). Pass ``None`` to
    stop. Set once at a run's start; best-effort (a logging failure never breaks the pipeline)."""
    _RUN_LOG_FILE.set(path)


def _run_file_sink(logger: Any, method_name: str, event_dict: dict) -> dict:
    """structlog processor: append the fully-bound event (level, timestamp, run_id, component, ...) as
    one JSON line to the current run's log file, then pass it through to the stdout renderer. Best-
    effort — any file error is swallowed so logging never breaks a run."""
    path = _RUN_LOG_FILE.get()
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event_dict, default=str, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return event_dict


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Configure structlog once. Safe to call repeatedly (idempotent).

    Logging must never hard-fail just because application settings are missing or invalid (e.g.
    importing the package before ``.env`` is set up). When ``get_settings()`` can't load, the log
    level/format fall back to safe defaults — the pipeline still validates config when it runs.
    """
    global _CONFIGURED
    if level is None or fmt is None:
        try:
            from .config import get_settings

            settings = get_settings()
            level = level or settings.log_level
            fmt = fmt or settings.log_format
        except Exception:
            level = level or "INFO"
            fmt = fmt or "json"
    level = level.upper()

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _run_file_sink,  # tee to the per-run log file (no-op until set_run_log_file is called)
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level, logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(**context: Any) -> structlog.stdlib.BoundLogger:
    """Return a logger, optionally pre-bound with context (e.g. ``run_id``)."""
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger().bind(**context)
