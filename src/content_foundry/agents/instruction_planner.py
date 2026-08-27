"""Agent 1.4 — Instruction Planner. Decomposes a creator's long-form ``--instructions`` into a ROUTED
plan so the whole run acts on the ask instead of dumping the raw paragraph into every prompt: what the
RESEARCH should go find/verify, concrete web-search queries to find it, and what the SCRIPT should
do/present. Gated by ``INSTRUCTION_PLANNER_ENABLED`` and only run when instructions are given.
Best-effort — any failure (or a disabled planner) degrades to a VERBATIM plan (the full instructions
steer both research and script, with no extra queries), so a run never breaks on it."""

from __future__ import annotations

import json
import re

from ..logging import get_logger
from ..models import InstructionPlan
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model

_MAX_QUERIES = 10
# Per-bucket cap. It SCALES with how much the creator wrote: a fixed ceiling silently discards the
# tail of a long brief, which is indistinguishable from the planner having ignored it. The floor keeps
# short instructions from producing a padded plan; the ceiling keeps a runaway list from crowding the
# prompts these buckets feed.
_MIN_ITEMS = 14
_MAX_ITEMS_CEILING = 40
_ITEMS_PER_REQUIREMENT = 2.0

# A requirement counts as COVERED when at least this fraction of its distinctive words appear
# somewhere in the plan. A fraction (not a count) because requirement sentences vary wildly in
# length, and a flat "share N words" bar silently becomes trivial for a long sentence and impossible
# for a short one.
_COVERED_FRACTION = 0.5
_MAX_PASSES = 3  # one plan + up to two focused repairs; each repair only sees what was missed

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_MIN_REQUIREMENT_WORDS = 4  # shorter fragments carry no checkable requirement
_MAX_ASKS = 60  # a brief with more distinct asks than this is not a brief any more


def _salient(text: str) -> set[str]:
    """Distinctive, lightly-stemmed words of ``text``.

    Imported LAZILY: ``pipeline/__init__`` pulls in the orchestrator, which imports this package, so
    a module-level import of anything under ``pipeline`` is a circular import even though
    ``topic_relevance`` itself is pure. Reused rather than reimplemented so requirement coverage and
    idea filtering agree on what counts as a distinctive word.
    """
    from ..pipeline.topic_relevance import _words

    return _words(text)


def requirements(instructions: str) -> list[str]:
    """Split a creator's brief into the atomic sentences a plan has to account for."""
    parts = _SENTENCE.split(instructions or "")
    return [s.strip() for s in parts if len(s.split()) >= _MIN_REQUIREMENT_WORDS]


def plan_vocabulary(plan: InstructionPlan) -> set[str]:
    """Every distinctive word the plan mentions, across all six buckets."""
    return _salient(
        " ".join(
            " ".join(items)
            for items in (
                plan.research_focus,
                plan.research_queries,
                plan.script_directions,
                plan.outline,
                plan.avoid,
                plan.terminology,
            )
        )
    )


def uncovered(reqs: list[str], plan: InstructionPlan) -> list[str]:
    """The requirements the plan never really mentions, so nothing downstream can act on them.

    Word-level rather than semantic on purpose: it is cheap, deterministic, and it is the same
    question the operator is asking -- "did my instruction survive into the plan at all?". A
    requirement can only be flagged when the plan is genuinely silent about its subject matter.
    """
    have = plan_vocabulary(plan)
    missed = []
    for req in reqs:
        want = _salient(req)
        if not want:
            continue
        if len(want & have) / len(want) < _COVERED_FRACTION:
            missed.append(req)
    return missed


class InstructionPlanner:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="instruction_planner")

    def plan(self, idea: str, instructions: str) -> InstructionPlan:
        """Route the instructions into research/query/script buckets, then REPAIR anything the first
        pass dropped.

        A single pass over a long brief reliably loses its tail -- and a lost instruction is invisible,
        because the plan still looks perfectly reasonable. So the requirements are extracted
        deterministically up front and the plan is checked against them; whatever is still unmentioned
        goes back to the planner ON ITS OWN, where it is the entire input rather than the last
        paragraph of a wall of text, and the result is merged in.

        The repair pass re-plans rather than pasting the raw sentence in, so every item reaching the
        downstream prompts is still the planner's own polished phrasing -- the creator's raw wording
        never leaks into the script prompt.

        Empty instructions -> empty plan (no LLM call); any parse/LLM failure -> the verbatim plan.
        """
        text = (instructions or "").strip()
        if not text:
            return InstructionPlan()
        reqs = self._enumerate_asks(text) or requirements(text)
        cap = _item_cap(len(reqs))

        plan = self._plan_once(idea, text, cap)
        if plan is None:
            return self.verbatim(text)

        for _ in range(_MAX_PASSES - 1):
            missed = uncovered(reqs, plan)
            if not missed:
                break
            self._log.info("instruction_plan_repairing", missed=len(missed), of=len(reqs))
            patch = self._plan_once(idea, "\n".join(missed), cap)
            if patch is None:
                break
            merged = _merge(plan, patch, cap)
            if merged == plan:  # the repair added nothing new; further passes cannot either
                break
            plan = merged

        still = uncovered(reqs, plan)
        self._log.info(
            "instruction_plan",
            requirements=len(reqs),
            uncovered=len(still),
            research=len(plan.research_focus),
            queries=len(plan.research_queries),
            script=len(plan.script_directions),
            outline=len(plan.outline),
            avoid=len(plan.avoid),
            terminology=len(plan.terminology),
        )
        if still:
            # Loud on purpose: the creator asked for something the plan never picked up, and a silent
            # drop is exactly the failure this whole mechanism exists to make visible.
            self._log.warning("instruction_plan_incomplete", uncovered=[s[:120] for s in still[:3]])
        return plan

    def _enumerate_asks(self, instructions: str) -> list[str]:
        """Decompose the brief into every atomic ask it contains, via one cheap LLM call.

        A creator's instructions are ALWAYS a paragraph, so there is no reliable punctuation signal
        for where one ask ends and the next begins -- a single sentence commonly carries five or six.
        Counting sentences therefore both under-reports the ask count and makes the later coverage
        check trivially passable. Decomposition is a language problem, so it goes to the model.

        Returns ``[]`` on any failure, and the caller falls back to the sentence split.
        """
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        try:
            resp = self._llm.complete(
                "Return ONLY the JSON now.",
                system=render_prompt(
                    load_prompt("instruction_asks.system"), instructions=instructions
                ),
                temperature=0.0,  # enumeration, not authorship
                max_tokens=self._settings.llm_max_tokens,
                model=model,
            )
            data = json.loads(extract_json(resp.text))
            asks = data.get("asks") if isinstance(data, dict) else None
            found = [s.strip() for s in asks or [] if isinstance(s, str) and s.strip()][:_MAX_ASKS]
        except Exception as exc:  # never fatal -- the sentence split still gives a usable check
            self._log.warning("instruction_asks_failed", error=str(exc))
            return []
        if found:
            self._log.info("instruction_asks", asks=len(found), chars=len(instructions))
        return found

    def _plan_once(self, idea: str, text: str, cap: int) -> InstructionPlan | None:
        """One planning call. ``None`` on any LLM/parse failure or an empty plan, so the caller can
        fall back to the verbatim steer rather than shipping a half-plan."""
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
            plan = self._coerce(data, cap)
        except Exception as exc:  # never fatal -- a bad plan must not block the run
            self._log.warning("instruction_plan_failed", error=str(exc))
            return None
        if plan.research_focus or plan.script_directions or plan.research_queries:
            return plan
        return None

    @staticmethod
    def verbatim(instructions: str) -> InstructionPlan:
        """The un-decomposed steer: the FULL instructions drive BOTH research and script (and no extra
        search queries) — i.e. the behaviour when the planner is off or fails."""
        text = (instructions or "").strip()
        if not text:
            return InstructionPlan()
        return InstructionPlan(research_focus=[text], script_directions=[text])

    @classmethod
    def _coerce(cls, data: object, cap: int = _MIN_ITEMS) -> InstructionPlan:
        if not isinstance(data, dict):
            return InstructionPlan()
        return InstructionPlan(
            research_focus=cls._strings(data.get("research_focus"), cap),
            research_queries=cls._strings(data.get("research_queries"), _MAX_QUERIES),
            script_directions=cls._strings(data.get("script_directions"), cap),
            outline=cls._strings(data.get("outline"), cap),
            avoid=cls._strings(data.get("avoid"), cap),
            terminology=cls._strings(data.get("terminology"), cap),
        )

    @staticmethod
    def _strings(val: object, cap: int) -> list[str]:
        if not isinstance(val, list):
            return []
        return [s.strip() for s in val if isinstance(s, str) and s.strip()][:cap]


def _item_cap(n_requirements: int) -> int:
    """Per-bucket cap, scaled to how much the creator actually wrote."""
    return max(_MIN_ITEMS, min(_MAX_ITEMS_CEILING, int(n_requirements * _ITEMS_PER_REQUIREMENT)))


def _dedupe(*lists: list[str]) -> list[str]:
    """Concatenate preserving order, dropping case-insensitive repeats."""
    seen: set[str] = set()
    out: list[str] = []
    for items in lists:
        for item in items:
            key = " ".join(item.lower().split())
            if key and key not in seen:
                seen.add(key)
                out.append(item)
    return out


def _merge(base: InstructionPlan, patch: InstructionPlan, cap: int) -> InstructionPlan:
    """Fold a repair pass into the plan, keeping the original items FIRST.

    Order matters: the first pass saw the whole brief and so ranks the ask as a whole, whereas a
    repair pass only saw the fragments that were missed and cannot judge their relative importance.
    """
    return InstructionPlan(
        research_focus=_dedupe(base.research_focus, patch.research_focus)[:cap],
        research_queries=_dedupe(base.research_queries, patch.research_queries)[:_MAX_QUERIES],
        script_directions=_dedupe(base.script_directions, patch.script_directions)[:cap],
        outline=_dedupe(base.outline, patch.outline)[:cap],
        avoid=_dedupe(base.avoid, patch.avoid)[:cap],
        terminology=_dedupe(base.terminology, patch.terminology)[:cap],
    )
