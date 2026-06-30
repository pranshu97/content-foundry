"""TelegramNotifier — one HTTPS POST to ``sendMessage`` (Ch. 25.2). Best-effort, never blocks."""

from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(1), reraise=True)
    def send(self, event: str, title: str, body: str, meta: dict | None = None) -> None:
        text = f"{title}\n{body}"
        resp = httpx.post(
            self._url,
            json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
