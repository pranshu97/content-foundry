"""Agent 1.5 — Researcher. Reads the real web pages behind the data brief and synthesizes a
source-backed DEPTH report (mechanisms: HOW/WHY things work) that the Script Generator draws on to
write insightful, non-obvious scenes. LLM synthesis grounded in fetched pages, with a deterministic
snippet fallback so a fetch or LLM failure never blocks the run."""

from __future__ import annotations

import html
import json
import re

import httpx

from ..errors import LLMError
from ..logging import get_logger
from ..models import Citation, DataBrief, KeyFact, ResearchBrief, ResearchPoint, utcnow
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json, run_interruptible
from ..providers.tiering import TaskTier, select_model

_UA = "content-foundry/1.0 (+https://github.com/)"
_SCRIPT_BLOCK_RE = re.compile(r"<(script|style|noscript|head)\b[^>]*>.*?</\1>", re.I | re.S)
_PARA_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(raw: str) -> str:
    """Crude, dependency-free HTML -> readable text: drop script/style/head, then prefer the article
    body (its <p> paragraphs) so nav/menus/footers are skipped; fall back to a full tag-strip when a
    page has no paragraphs. Entities are unescaped and whitespace collapsed."""
    cleaned = _SCRIPT_BLOCK_RE.sub(" ", raw)
    paragraphs = _PARA_RE.findall(cleaned)
    body = " ".join(paragraphs) if paragraphs else cleaned
    text = _TAG_RE.sub(" ", body)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def fetch_article_text(url: str, *, max_chars: int = 4000, timeout: float = 10.0) -> str:
    """Best-effort: fetch a page and return its readable text (truncated). Returns "" on ANY failure
    (paywall, block, timeout, non-HTML) — a failed source is simply skipped, never fatal."""
    try:
        resp = run_interruptible(
            lambda: httpx.get(
                url, headers={"User-Agent": _UA}, timeout=timeout, follow_redirects=True
            )
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if ctype and "html" not in ctype and "text" not in ctype:
            return ""
        return _html_to_text(resp.text)[:max_chars]
    except Exception:  # pragma: no cover - network
        return ""


def research_key_facts(research: ResearchBrief) -> list[KeyFact]:
    """Turn the Researcher's findings into CITABLE KeyFacts so the Script Generator can GROUND the
    chosen idea in idea-relevant, source-backed specifics (its own numbers/examples) instead of
    pivoting to whatever unrelated numbers happen to be in the raw feed (the salary-drift bug)."""
    return [
        KeyFact(
            statement=point.point,
            citation=Citation(
                source="research",
                url=point.source_url,
                observed_at=utcnow(),
                snippet=(point.evidence.strip() or point.point),
            ),
        )
        for point in research.points
        if point.point
    ]


class Researcher:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="research")

    def run(self, run_id: str, brief: DataBrief, *, idea: str) -> ResearchBrief:
        sources = self._gather_sources(brief)
        points, used_model = self._synthesize(idea, brief.niche, sources)
        if not points:
            points = self._fallback(brief)
            used_model = None
        return ResearchBrief(
            run_id=run_id,
            idea=idea,
            points=points[: self._settings.research_max_points],
            source_urls=[url for url, _ in sources],
            used_model=used_model,
        )

    def _gather_sources(self, brief: DataBrief) -> list[tuple[str, str]]:
        """Fetch the full text behind the brief's citation URLs (deduped, capped). Falls back to the
        citation snippet for any page that cannot be fetched, so there is always something to read."""
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for fact in brief.key_facts:
            url = (fact.citation.url or "").strip()
            if not url or url.lower() in seen:
                continue
            seen.add(url.lower())
            text = fetch_article_text(
                url,
                max_chars=self._settings.research_max_chars_per_source,
                timeout=self._settings.research_fetch_timeout_sec,
            )
            if not text:
                text = (fact.citation.snippet or fact.statement or "").strip()
            if text:
                out.append((url, text))
            if len(out) >= self._settings.research_max_sources:
                break
        return out

    def _synthesize(self, idea, niche, sources) -> tuple[list[ResearchPoint], str | None]:
        if not sources:
            return [], None
        blocks = "\n\n".join(f"[{url}]\n{text}" for url, text in sources)
        model = select_model(
            self._settings, TaskTier.HEAVY, fallback=self._settings.generator_model
        )
        try:
            system = render_prompt(
                load_prompt("research.system"),
                niche=niche,
                idea=idea or niche,
                max_points=str(self._settings.research_max_points),
                sources=blocks,
            )
            resp = self._llm.complete(
                "Return ONLY the JSON now.",
                system=system,
                temperature=max(self._settings.llm_temperature, 0.3),
                max_tokens=self._settings.llm_max_tokens,
                model=model,
            )
            # The model may return a bare array or a {"points": [...]} object; extract_json only
            # recovers objects, so try a direct parse first, then fall back to it.
            try:
                data = json.loads(resp.text.strip())
            except json.JSONDecodeError:
                data = json.loads(extract_json(resp.text))
            if isinstance(data, dict):
                data = data.get("points") or data.get("items") or []
            points = [
                self._coerce_point(d) for d in data if isinstance(d, dict) and d.get("point")
            ]
            if points:
                self._log.info("research_points", count=len(points))
                return points, model
        except (json.JSONDecodeError, LLMError, ValueError, AttributeError, TypeError) as exc:
            self._log.warning("research_synthesis_failed", error=str(exc))
        return [], None

    @staticmethod
    def _coerce_point(d: dict) -> ResearchPoint:
        return ResearchPoint(
            point=str(d.get("point", "")).strip(),
            explanation=str(d.get("explanation", "") or "").strip(),
            evidence=str(d.get("evidence", "") or "").strip(),
            source_url=(str(d["source_url"]).strip() if d.get("source_url") else None),
        )

    def _fallback(self, brief: DataBrief) -> list[ResearchPoint]:
        """Deterministic: turn the top facts/snippets into bare points so the generator still gets
        SOME structured material when the LLM or every page fetch failed."""
        return [
            ResearchPoint(
                point=fact.statement,
                evidence=(fact.citation.snippet or "").strip(),
                source_url=fact.citation.url,
            )
            for fact in brief.key_facts[: self._settings.research_max_points]
        ]
