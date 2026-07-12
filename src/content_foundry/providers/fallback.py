"""FallbackProvider — try the primary LLM, then a secondary on failure (Ch. 3.4, 21.2).

If the primary hits a rate limit / quota (:class:`LLMRateLimitError`) it is LATCHED OFF for the rest
of this run: every later call goes straight to the secondary. So a Google free-tier exhaustion
mid-run switches the whole run to the local model instead of retrying (and failing) on every call.
A one-off transient error still falls back for that single call but keeps trying the primary next
time (it may have just been a blip).
"""

from __future__ import annotations

from ..errors import LLMError, LLMRateLimitError
from ..logging import get_logger
from .base import LLMProvider, LLMResponse


class FallbackProvider:
    """Wraps a primary provider and an optional secondary used only on primary failure."""

    name = "fallback"

    def __init__(
        self, primary: LLMProvider, secondary: LLMProvider | None = None, *, latch_all: bool = False
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        # Latch the primary OFF for the rest of the run once it hits a rate limit / quota. With
        # ``latch_all`` it also latches on ANY error — used for the intra-Google model chain, where a
        # model that errors (bad id, quota, outage) should be abandoned for a healthier one, not
        # retried every call. The OUTER provider fallback (e.g. google->local) keeps latch_all=False,
        # so a transient google blip only falls back for that one call and retries google next time.
        self._latch_all = latch_all
        self._primary_disabled = False  # latched True once the primary is abandoned for the run
        self._log = get_logger(component="llm_fallback")

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        kwargs = {
            "system": system,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "model": model,
        }
        # Once the primary has been rate-limited this run, skip it entirely and serve from the fallback.
        if self._primary_disabled and self.secondary is not None:
            return self.secondary.complete(prompt, **kwargs)
        try:
            return self.primary.complete(prompt, **kwargs)
        except LLMError as exc:
            if self.secondary is None:
                raise
            latch = isinstance(exc, LLMRateLimitError) or self._latch_all
            self._primary_disabled = self._primary_disabled or latch
            # Surface WHICH model failed and WHY on the output screen — a chain of these lines shows
            # the user each Google model that was tried and skipped (quota/429 vs a bad id/404 etc.).
            self._log.warning(
                "llm_provider_failed_trying_next",
                failed=getattr(self.primary, "_model", None) or getattr(self.primary, "name", "?"),
                rate_limited=isinstance(exc, LLMRateLimitError),
                latched=latch,
                reason=str(exc)[:200],
            )
            return self.secondary.complete(prompt, **kwargs)
