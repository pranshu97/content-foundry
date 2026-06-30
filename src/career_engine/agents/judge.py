"""Agent 3 — Judge. Deterministic-first quality gate with an optional LLM pass (Ch. 9)."""

from __future__ import annotations

import json
import random

from ..errors import LLMError
from ..logging import get_logger
from ..models import DataBrief, DimensionScore, JudgeReport, Provenance, Script, Verdict
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..safeguards.grounding import check_grounding
from ..templates import select_template
from .judge_checks import (
    compliance_check,
    freshness_and_fatigue,
    heuristic_actionability,
    heuristic_insight,
    hook_score,
    specificity_score,
)

# Spec weights (Ch. 9.3) sum to 1.10; weighted_total is normalised by the total weight -> 0-10.
WEIGHTS = {
    "actionability": 0.20,
    "specificity": 0.20,
    "grounding": 0.20,
    "insight": 0.20,
    "hook": 0.15,
    "freshness": 0.10,
    "compliance": 0.05,
}
_TOTAL_WEIGHT = sum(WEIGHTS.values())


class Judge:
    def __init__(self, settings, llm_provider: LLMProvider | None = None):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="judge")

    def run(
        self,
        run_id: str,
        script: Script,
        brief: DataBrief,
        *,
        attempt_number: int,
        recent_template_ids: list[str] | None = None,
        recent_hooks: list[str] | None = None,
    ) -> JudgeReport:
        recent_template_ids = recent_template_ids or []
        recent_hooks = recent_hooks or []
        s = self._settings

        # ---- deterministic checks (no tokens) ----
        grounding = check_grounding(script, brief)
        comp_score, comp_ok = compliance_check(script)
        spec = specificity_score(script)
        hk = hook_score(script)
        fresh = freshness_and_fatigue(
            script.template_id, script.hook, recent_template_ids, recent_hooks
        )

        hard_gate_failed = (not comp_ok) or (grounding.score < s.grounding_min)

        # ---- subjective dims: LLM (hybrid/llm) or heuristic (deterministic / fallback) ----
        act, ins, score_1_5, evidence, justif, used_model = self._subjective_scores(
            script, hard_gate_failed
        )

        # ---- forced shift target on fatigue (deterministic) ----
        forced_template_id = None
        if fresh.fatigue:
            forced_template_id = select_template(
                recent_template_ids, exclude=script.template_id, rng=random.Random(0)
            ).id

        # ---- assemble dimension scores ----
        raw = {
            "actionability": act,
            "specificity": spec,
            "grounding": grounding.score,
            "insight": ins,
            "hook": hk,
            "freshness": fresh.score,
            "compliance": comp_score,
        }
        floors = {"grounding": s.grounding_min, "insight": s.insight_min}
        dimensions = [
            self._dimension(
                name,
                raw[name],
                floor=floors.get(name),
                compliance_ok=comp_ok if name == "compliance" else None,
                score_1_5=score_1_5.get(name),
                evidence=evidence.get(name),
                justification=justif.get(name),
                forced_template_id=forced_template_id,
            )
            for name in WEIGHTS
        ]

        weighted_total = round(
            sum(d.score * d.weight for d in dimensions) / _TOTAL_WEIGHT, 2
        )

        verdict = self._verdict(
            weighted_total=weighted_total,
            compliance_ok=comp_ok,
            grounding_score=grounding.score,
            insight_score=ins,
            fatigue=fresh.fatigue,
            attempt_number=attempt_number,
        )

        revision_instructions = (
            None if verdict == Verdict.PASS else self._revision_instructions(dimensions, fresh, forced_template_id)
        )

        return JudgeReport(
            run_id=run_id,
            attempt_number=attempt_number,
            template_id=script.template_id,
            scores=dimensions,
            weighted_total=weighted_total,
            insight_score=ins,
            grounding_score=grounding.score,
            template_fatigue=fresh.fatigue,
            force_shift=fresh.fatigue,
            forced_template_id=forced_template_id,
            verdict=verdict,
            summary=self._summary(verdict, weighted_total, fresh.fatigue),
            revision_instructions=revision_instructions,
            provenance=Provenance(
                produced_by="judge", model=used_model, config_hash=s.config_hash
            ),
        )

    # ------------------------------------------------------------- subjective
    def _subjective_scores(self, script: Script, hard_gate_failed: bool):
        mode = self._settings.judge_mode
        want_llm = mode in ("hybrid", "llm") and (mode == "llm" or not hard_gate_failed)

        if want_llm and self._llm is not None:
            try:
                a, i = self._llm_scores(script)
                act = (int(a["score_1_5"]) - 1) * 2.5
                ins = (int(i["score_1_5"]) - 1) * 2.5
                return (
                    round(act, 2),
                    round(ins, 2),
                    {"actionability": int(a["score_1_5"]), "insight": int(i["score_1_5"])},
                    {"actionability": a.get("evidence"), "insight": i.get("evidence")},
                    {
                        "actionability": a.get("justification", "LLM-scored"),
                        "insight": i.get("justification", "LLM-scored"),
                    },
                    self._settings.judge_model,
                )
            except (LLMError, KeyError, ValueError, json.JSONDecodeError) as exc:
                self._log.warning("judge_llm_fallback", error=str(exc))

        # deterministic / fallback heuristics
        return (
            heuristic_actionability(script),
            heuristic_insight(script),
            {},
            {},
            {"actionability": "heuristic", "insight": "heuristic"},
            None,
        )

    def _llm_scores(self, script: Script) -> tuple[dict, dict]:
        system = render_prompt(
            load_prompt("judge.system"),
            rubric_text=load_prompt("judge.rubric"),
            script_json=script.model_dump_json(),
        )
        resp = self._llm.complete(
            "Return ONLY the JSON now.",
            system=system,
            temperature=self._settings.judge_temperature,
            model=self._settings.judge_model,
        )
        data = json.loads(extract_json(resp.text))
        return data["actionability"], data["insight"]

    # ------------------------------------------------------------- assembly
    def _dimension(
        self,
        name: str,
        score: float,
        *,
        floor: float | None,
        compliance_ok: bool | None,
        score_1_5: int | None,
        evidence: str | None,
        justification: str | None,
        forced_template_id: str | None,
    ) -> DimensionScore:
        if compliance_ok is not None:
            passed = compliance_ok
        elif floor is not None:
            passed = score >= floor
        else:
            passed = True
        fix = None if (passed and score >= 7.0) else self._fix_for(name, forced_template_id)
        return DimensionScore(
            dimension=name,
            score_1_5=score_1_5,
            score=round(score, 2),
            weight=WEIGHTS[name],
            minimum=floor,
            passed=passed,
            evidence=evidence,
            justification=justification or f"{name} scored {round(score, 2)}/10.",
            fix_suggestion=fix,
        )

    @staticmethod
    def _fix_for(name: str, forced_template_id: str | None) -> str:
        return {
            "actionability": "Give 2-3 concrete, specific steps the viewer can do this week.",
            "specificity": "Name concrete numbers, roles, and tools from the data.",
            "grounding": "Tie every number to a DataBrief fact_ref; remove invented stats.",
            "insight": "Add a non-obvious, data-backed reframing; cut clichés.",
            "hook": "Open with a specific number or claim in the first ~10 seconds.",
            "freshness": f"Switch structure to {forced_template_id or 'a different template'}; vary the hook.",
            "compliance": "Set synthetic_disclosure and add a synthetic-content note to the description.",
        }.get(name, "Improve this dimension.")

    def _verdict(
        self,
        *,
        weighted_total: float,
        compliance_ok: bool,
        grounding_score: float,
        insight_score: float,
        fatigue: bool,
        attempt_number: int,
    ) -> Verdict:
        s = self._settings
        gate_failed = (
            (not compliance_ok)
            or grounding_score < s.grounding_min
            or insight_score < s.insight_min
            or fatigue
        )
        if not gate_failed and weighted_total >= s.pass_threshold:
            return Verdict.PASS
        if attempt_number >= s.max_revisions:
            return Verdict.FAIL
        return Verdict.REVISE

    @staticmethod
    def _revision_instructions(dimensions, fresh, forced_template_id) -> str:
        parts = [d.fix_suggestion for d in dimensions if d.fix_suggestion]
        if fresh.fatigue and forced_template_id:
            parts.insert(0, f"Template fatigue detected — switch to '{forced_template_id}'.")
        # de-duplicate while preserving order
        seen: set[str] = set()
        unique = [p for p in parts if not (p in seen or seen.add(p))]
        return " ".join(unique)

    @staticmethod
    def _summary(verdict: Verdict, weighted_total: float, fatigue: bool) -> str:
        if verdict == Verdict.PASS:
            return f"Approved for production (weighted {weighted_total}/10)."
        tail = " Template fatigue forced a structural shift." if fatigue else ""
        return f"Verdict {verdict.value} at {weighted_total}/10.{tail}"
