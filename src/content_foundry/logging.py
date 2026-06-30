"""structlog setup with run-scoped binding (Ch. 21.5)."""

from __future__ import annotations

import logging
from typing import Any

import structlog

_CONFIGURED = False


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
