"""Exception hierarchy for Content Foundry (Ch. 21.1).

All errors derive from :class:`CareerEngineError` so callers can catch the whole family.
Filenames for this hierarchy are not pinned by the spec; centralising them here gives every
layer a single import site.
"""

from __future__ import annotations


class CareerEngineError(Exception):
    """Base class for every engine-specific error."""


class ConfigError(CareerEngineError):
    """Bad/missing settings — fail fast at startup (exit code 2)."""


class DataSourceError(CareerEngineError):
    """A single data source failed (recoverable; degrade gracefully)."""


class NoDataError(CareerEngineError):
    """Every data source failed — the run cannot proceed."""


class InsufficientDataError(CareerEngineError):
    """Fewer than ``MIN_FACTS`` grounded facts were produced."""


class LLMError(CareerEngineError):
    """LLM provider failure after retries (and fallback, if any)."""


class BudgetExhaustedError(CareerEngineError):
    """Estimated month-to-date spend reached the budget cap; the run is aborted before more spend."""


class SchemaValidationError(CareerEngineError):
    """An artifact/JSON payload failed schema or schema-version validation."""


class GroundingError(CareerEngineError):
    """An ungrounded claim could not be repaired."""


class RenderError(CareerEngineError):
    """ffmpeg/render-backend failure."""


class PublishError(CareerEngineError):
    """Upload/auth/quota failure during publishing."""
