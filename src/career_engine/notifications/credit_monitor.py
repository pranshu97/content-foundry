"""Credit / budget monitoring → debounced ``low_credits`` alerts (Ch. 25.5).

Storage-agnostic by design: the orchestrator loads the month-to-date total from the DB, seeds the
monitor, then persists the updated total afterwards — so this module never imports persistence.
"""

from __future__ import annotations

from .base import Notifier

# Rough USD per 1K tokens (input, output). Used only for *estimated* spend alerts.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-20250514": (0.003, 0.015),
    "claude-3-5-haiku-20241022": (0.0008, 0.004),
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "default": (0.003, 0.015),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = PRICE_TABLE.get(model, PRICE_TABLE["default"])
    return (prompt_tokens / 1000.0) * in_price + (completion_tokens / 1000.0) * out_price


class CreditMonitor:
    def __init__(
        self,
        notifier: Notifier,
        *,
        budget_usd: float,
        threshold_pct: float,
        month_to_date_usd: float = 0.0,
    ) -> None:
        self._notifier = notifier
        self._budget = max(budget_usd, 0.0001)
        self._threshold_pct = threshold_pct
        self.month_to_date_usd = month_to_date_usd
        self._alerted = False

    @property
    def pct_used(self) -> float:
        return 100.0 * self.month_to_date_usd / self._budget

    def record(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        cost = estimate_cost(model, prompt_tokens, completion_tokens)
        self.month_to_date_usd += cost
        self._maybe_alert()
        return cost

    def record_provider_error(self, provider: str, message: str) -> None:
        """Any insufficient-quota/credit provider error fires an immediate alert (Ch. 25.5)."""
        self._notifier.send(
            "low_credits",
            "⚠️ Provider credit/quota issue",
            f"{provider}: {message}",
            meta={"provider": provider},
        )

    def _maybe_alert(self) -> None:
        if not self._alerted and self.pct_used >= self._threshold_pct:
            self._alerted = True
            self._notifier.send(
                "low_credits",
                "⚠️ Low credits",
                f"Projected spend at {self.pct_used:.0f}% of ${self._budget:.2f} monthly budget.",
                meta={"pct_used": round(self.pct_used, 1)},
            )
