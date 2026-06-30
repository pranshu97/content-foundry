"""Build a notifier from config + an event-filtering, fail-soft wrapper (Ch. 25.3, 25.7)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..logging import get_logger
from .base import Notifier, NullNotifier

if TYPE_CHECKING:
    from ..config import Settings

_log = get_logger(component="notifier")


class EventFilterNotifier:
    """Drops events not in ``allowed`` and makes every send best-effort (never raises)."""

    def __init__(self, inner: Notifier, allowed: Iterable[str]) -> None:
        self._inner = inner
        self._allowed = set(allowed)

    def send(self, event: str, title: str, body: str, meta: dict | None = None) -> None:
        if event not in self._allowed:
            return
        try:
            self._inner.send(event, title, body, meta)
        except Exception as exc:  # delivery failure must never fail a run (Ch. 25.7)
            _log.warning("notification_failed", event_name=event, error=str(exc))


def build_notifier(settings: Settings) -> Notifier:
    if not settings.notify_enabled or settings.notifier == "none":
        return NullNotifier()
    if settings.notifier == "telegram":
        from .telegram import TelegramNotifier

        inner: Notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    else:  # pragma: no cover - exhaustive guard
        inner = NullNotifier()
    return EventFilterNotifier(inner, settings.notify_events_list)
