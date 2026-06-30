"""Unit: notifications + credit monitor (Ch. 25)."""

from __future__ import annotations

from career_engine.notifications import (
    CreditMonitor,
    EventFilterNotifier,
    NullNotifier,
    build_notifier,
    estimate_cost,
)


def test_null_notifier_records():
    n = NullNotifier()
    n.send("run_complete", "t", "b")
    assert n.sent == [("run_complete", "t", "b", None)]


def test_event_filter_drops_unlisted_events():
    inner = NullNotifier()
    filt = EventFilterNotifier(inner, {"run_complete"})
    filt.send("run_complete", "t", "b")
    filt.send("low_credits", "t", "b")  # dropped
    assert [c[0] for c in inner.sent] == ["run_complete"]


def test_event_filter_is_fail_soft():
    class Boom:
        def send(self, *a, **k):
            raise RuntimeError("network down")

    filt = EventFilterNotifier(Boom(), {"run_complete"})
    filt.send("run_complete", "t", "b")  # must not raise


def test_build_notifier_disabled_returns_null(monkeypatch):
    from career_engine.config import get_settings, reset_settings_cache

    monkeypatch.setenv("NOTIFY_ENABLED", "false")
    reset_settings_cache()
    assert isinstance(build_notifier(get_settings()), NullNotifier)


def test_estimate_cost_positive():
    assert estimate_cost("claude-sonnet-4-20250514", 1000, 1000) > 0
    assert estimate_cost("unknown-model", 1000, 0) > 0


def test_credit_monitor_fires_once_over_threshold():
    notifier = NullNotifier()
    monitor = CreditMonitor(notifier, budget_usd=0.01, threshold_pct=50, month_to_date_usd=0.0)
    monitor.record("claude-sonnet-4-20250514", 100000, 100000)  # large -> crosses threshold
    monitor.record("claude-sonnet-4-20250514", 100000, 100000)  # debounced
    low = [c for c in notifier.sent if c[0] == "low_credits"]
    assert len(low) == 1


def test_credit_monitor_provider_error_alert():
    notifier = NullNotifier()
    monitor = CreditMonitor(notifier, budget_usd=100, threshold_pct=80)
    monitor.record_provider_error("openai", "insufficient_quota")
    assert notifier.sent[-1][0] == "low_credits"
