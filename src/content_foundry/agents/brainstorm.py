"""Agent 0 — Brainstormer. Proposes several fresh, specific, helpful video ideas from the brief so
runs don't collapse onto the same topic. LLM-driven with a deterministic content-angle fallback."""

from __future__ import annotations

import json

from ..errors import LLMError
from ..logging import get_logger
from ..models import DataBrief
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model


class Brainstormer:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="brainstorm")

    def propose(
        self, brief: DataBrief, *, recent_ideas: list[str] | None = None, count: int = 5,
        focus: str = "",
    ) -> list[str]:
        """Propose ``count`` distinct, specific video ideas. ``focus`` (e.g. the user's --idea) steers
        every idea. The fetched data is used only as INSPIRATION / supporting stats, never the whole
        basis. Falls back to deterministic content angles when the LLM is unavailable."""
        recent = [r for r in (recent_ideas or []) if r]
        fallback = self._fallback(brief, recent, focus, count)
        try:
            system = render_prompt(
                load_prompt("brainstorm.system"),
                niche=brief.niche,
                count=str(count),
                focus_line=(
                    f'The viewer specifically asked for: "{focus}". EVERY idea MUST be a concrete, '
                    "specific angle that delivers on this." if focus else ""
                ),
                facts_json=json.dumps([kf.statement for kf in brief.key_facts], ensure_ascii=False),
                avoid_json=json.dumps(recent[:8], ensure_ascii=False),
            )
            resp = self._llm.complete(
                "Return ONLY the JSON array now.",
                system=system,
                temperature=max(self._settings.llm_temperature, 0.8),
                max_tokens=700,
                model=select_model(
                    self._settings, TaskTier.HEAVY, fallback=self._settings.generator_model
                ),
            )
            data = json.loads(extract_json(resp.text))
            if isinstance(data, dict):
                data = data.get("ideas") or data.get("items") or []
            ideas = _dedup(data) if isinstance(data, list) else []
            if ideas:
                self._log.info("brainstormed_ideas", count=len(ideas))
                return ideas[:count]
        except (json.JSONDecodeError, LLMError, KeyError, ValueError, AttributeError, TypeError) as exc:
            self._log.warning("brainstorm_fallback", error=str(exc))
        return fallback

    def run(self, brief: DataBrief, *, recent_ideas: list[str] | None = None) -> str:
        """Back-compat single-idea helper."""
        ideas = self.propose(brief, recent_ideas=recent_ideas, count=1)
        return ideas[0] if ideas else ""

    def _fallback(self, brief: DataBrief, recent: list[str], focus: str, count: int) -> list[str]:
        """Deterministic: content angles (plus a couple seeded on ``focus``), skipping recent ones."""
        pool: list[str] = []
        if focus:
            pool += [
                f"{focus.strip().capitalize()}: what the {brief.niche} data actually says",
                f"{count} {focus.strip()} moves that beat the {brief.niche} market",
            ]
        pool += [a.hook for a in brief.content_angles]
        if not pool:
            pool = [f"A specific, data-backed {brief.niche} explainer that solves one real problem"]
        recent_l = " ".join(recent).lower()
        fresh = [h for h in _dedup(pool) if h[:25].lower() not in recent_l]
        return (fresh or _dedup(pool))[:count]


def _dedup(items) -> list[str]:
    """Order-preserving de-dup + trim; coerces non-strings to str."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = (item if isinstance(item, str) else str(item)).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out
