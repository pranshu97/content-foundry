"""Agent 1.4 — Instruction Planner. Decomposes a creator's long-form ``--instructions`` into a ROUTED
plan so the whole run acts on the ask instead of dumping the raw paragraph into every prompt: what the
RESEARCH should go find/verify, concrete web-search queries to find it, and what the SCRIPT should
do/present. Gated by ``INSTRUCTION_PLANNER_ENABLED`` and only run when instructions are given.
Best-effort — any failure (or a disabled planner) degrades to a VERBATIM plan (the full instructions
steer both research and script, with no extra queries), so a run never breaks on it."""

from __future__ import annotations

import json

from ..logging import get_logger
from ..models import InstructionPlan
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model

_MAX_QUERIES = 10
_MAX_ITEMS = 14  # any single bucket; a runaway list would crowd the prompts it feeds


class InstructionPlanner:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="instruction_planner")

    def plan(self, idea: str, instructions: str) -> InstructionPlan:
        """Route the instructions into research/query/script buckets. Empty instructions -> empty plan
        (no LLM call); any parse/LLM failure -> the verbatim plan."""
        text = (instructions or "").strip()
        if not text:
            return InstructionPlan()
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        try:
            system = render_prompt(
                load_prompt("instruction_planner.system"),
                idea=idea or self._settings.target_niche,
                instructions=text,
            )
            resp = self._llm.complete(
                "Return ONLY the JSON now.",
                system=system,
                temperature=min(
                    self._settings.llm_temperature, 0.3
                ),  # low: deterministic structuring
                max_tokens=self._settings.llm_max_tokens,
                model=model,
            )
            try:
                data = json.loads(resp.text.strip())
            except json.JSONDecodeError:
                data = json.loads(extract_json(resp.text))
            plan = self._coerce(data)
            if plan.research_focus or plan.script_directions or plan.research_queries:
                self._log.info(
                    "instruction_plan",
                    research=len(plan.research_focus),
                    queries=len(plan.research_queries),
                    script=len(plan.script_directions),
                    outline=len(plan.outline),
                    avoid=len(plan.avoid),
                    terminology=len(plan.terminology),
                )
                return plan
        except Exception as exc:  # never fatal — a bad plan must not block the run
            self._log.warning("instruction_plan_failed", error=str(exc))
        return self.verbatim(text)

    @staticmethod
    def verbatim(instructions: str) -> InstructionPlan:
        """The un-decomposed steer: the FULL instructions drive BOTH research and script (and no extra
        search queries) — i.e. the behaviour when the planner is off or fails."""
        text = (instructions or "").strip()
        if not text:
            return InstructionPlan()
        return InstructionPlan(research_focus=[text], script_directions=[text])

    @classmethod
    def _coerce(cls, data: object) -> InstructionPlan:
        if not isinstance(data, dict):
            return InstructionPlan()
        return InstructionPlan(
            research_focus=cls._strings(data.get("research_focus")),
            research_queries=cls._strings(data.get("research_queries"))[:_MAX_QUERIES],
            script_directions=cls._strings(data.get("script_directions")),
            outline=cls._strings(data.get("outline")),
            avoid=cls._strings(data.get("avoid")),
            terminology=cls._strings(data.get("terminology")),
        )

    @staticmethod
    def _strings(val: object) -> list[str]:
        if not isinstance(val, list):
            return []
        return [s.strip() for s in val if isinstance(s, str) and s.strip()][:_MAX_ITEMS]
