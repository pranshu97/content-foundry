"""Notifier protocol + NullNotifier (Ch. 25.3)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    def send(self, event: str, title: str, body: str, meta: dict | None = None) -> None: ...


class NullNotifier:
    """No-op notifier (NOTIFY_ENABLED=false / NOTIFIER=none / tests). Records calls for assertions."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, dict | None]] = []

    def send(self, event: str, title: str, body: str, meta: dict | None = None) -> None:
        self.sent.append((event, title, body, meta))
