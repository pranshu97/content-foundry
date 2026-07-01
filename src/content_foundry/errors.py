"""Exception hierarchy for Content Foundry (Ch. 21.1).

All errors derive from :class:`ContentFoundryError` so callers can catch the whole family.
Filenames for this hierarchy are not pinned by the spec; centralising them here gives every
layer a single import site.
"""

from __future__ import annotations


class ContentFoundryError(Exception):
    """Base class for every engine-specific error."""


class ConfigError(ContentFoundryError):
    """Bad/missing settings — fail fast at startup (exit code 2)."""


class DataSourceError(ContentFoundryError):
    """A single data source failed (recoverable; degrade gracefully)."""


class NoDataError(ContentFoundryError):
    """Every data source failed — the run cannot proceed."""


class InsufficientDataError(ContentFoundryError):
    """Fewer than ``MIN_FACTS`` grounded facts were produced."""


class LLMError(ContentFoundryError):
    """LLM provider failure after retries (and fallback, if any)."""


class BudgetExhaustedError(ContentFoundryError):
    """Estimated month-to-date spend reached the budget cap; the run is aborted before more spend."""


class SchemaValidationError(ContentFoundryError):
    """An artifact/JSON payload failed schema or schema-version validation."""


class GroundingError(ContentFoundryError):
    """An ungrounded claim could not be repaired."""


class TTSError(ContentFoundryError):
    """Text-to-speech synthesis failed (missing voice model/binary, provider error, or bad audio)."""


class RenderError(ContentFoundryError):
    """ffmpeg/render-backend failure."""


class PublishError(ContentFoundryError):
    """Upload/auth/quota failure during publishing."""
