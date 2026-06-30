"""Layoffs feed source — parses an RSS endpoint with the stdlib (no lxml dep) (Ch. 3.5)."""

from __future__ import annotations

import re
from xml.etree import ElementTree

import httpx
from dateutil import parser as dateparser

from ..errors import DataSourceError
from ..models import NormalizedSignal, utcnow
from .base import DEFAULT_TIMEOUT

_HEADCOUNT_RE = re.compile(r"([\d,]{2,})\s+(?:employees|workers|staff|jobs|people)", re.I)


class LayoffsSource:
    name = "layoffs"

    def __init__(self, feed_url: str, *, max_items: int = 15) -> None:
        self._feed_url = feed_url
        self._max_items = max_items

    def fetch(self) -> list[NormalizedSignal]:
        try:
            resp = httpx.get(self._feed_url, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            root = ElementTree.fromstring(resp.text)
        except Exception as exc:
            raise DataSourceError(f"Layoffs feed fetch failed: {exc}") from exc

        signals: list[NormalizedSignal] = []
        for item in root.iter("item"):
            title = _text(item, "title")
            if not title:
                continue
            description = _text(item, "description") or ""
            headcount = _extract_headcount(f"{title} {description}")
            signals.append(
                NormalizedSignal(
                    source=self.name,
                    kind="layoff",
                    title=title,
                    value=headcount,
                    unit="employees" if headcount else None,
                    observed_at=_safe_dt(_text(item, "pubDate")),
                    url=_text(item, "link"),
                    raw={"description": description},
                )
            )
            if len(signals) >= self._max_items:
                break
        return signals


def _text(item: ElementTree.Element, tag: str) -> str | None:
    el = item.find(tag)
    return el.text.strip() if el is not None and el.text else None


def _extract_headcount(text: str) -> str | None:
    match = _HEADCOUNT_RE.search(text)
    return match.group(1).replace(",", "") if match else None


def _safe_dt(value: object):
    if not value:
        return utcnow()
    try:
        return dateparser.parse(str(value))
    except (ValueError, TypeError):
        return utcnow()
