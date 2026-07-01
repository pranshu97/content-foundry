"""Agent 3 — Judge. Deterministic-first quality gate with an optional LLM pass (Ch. 9)."""

from __future__ import annotations

import json
import random

from ..errors import LLMError
from ..logging import get_logger
from ..models import DataBrief, DimensionScore, JudgeReport, Provenance, Script, Verdict
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model
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

        # An egregiously short draft (a single scene) is rejected without spending an LLM call,
        # exactly like a grounding/compliance violation. Full completeness (scene/word floors) is
        # evaluated after the weighted total, so a high-scoring draft can earn a little slack.
        hard_gate_failed = (
            (not comp_ok) or (grounding.score < s.grounding_min) or (len(script.scenes) < 2)
        )

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

        # ---- gate relief: a genuinely excellent draft earns a little slack on the *quality/quantity*
        # floors (insight & length) — NEVER on grounding, compliance, or anti-repetition. ----
        strict_floor = int(s.min_script_word_ratio * s.script_target_words)
        relief = s.gate_relief_ratio if weighted_total >= s.gate_relief_score else 0.0
        factor = 1.0 - relief
        word_floor = int(strict_floor * factor)
        min_scenes_eff = max(2, round(s.min_scenes * factor))
        completeness_ok = (
            len(script.scenes) >= min_scenes_eff and script.word_count >= word_floor
        )
        insight_ok = ins >= s.insight_min * factor
        strict_complete = len(script.scenes) >= s.min_scenes and script.word_count >= strict_floor
        gates_relaxed = relief > 0.0 and (
            (completeness_ok and not strict_complete) or (insight_ok and ins < s.insight_min)
        )

        verdict = self._verdict(
            weighted_total=weighted_total,
            compliance_ok=comp_ok,
            grounding_score=grounding.score,
            insight_ok=insight_ok,
            fatigue=fresh.fatigue,
            completeness_ok=completeness_ok,
            attempt_number=attempt_number,
        )

        length_note = None
        if not completeness_ok:
            shortfall = max(0, word_floor - script.word_count)
            per_scene = max(40, round(s.script_target_words / max(s.scenes_per_video, 1)))
            length_note = (
                f"LENGTH — HARD FAIL (this is why it did not pass): your draft is only "
                f"{script.word_count} words in {len(script.scenes)} scene(s); the REQUIRED minimum is "
                f"{word_floor} words (target ~{s.script_target_words}). You are ~{shortfall} words short. "
                f"Do NOT delete anything — EXPAND: write {s.scenes_per_video} scenes, each at least "
                f"{per_scene} words (4-6 full sentences), adding concrete detail, examples, and the data "
                f"to every scene. Any script under {word_floor} words is automatically rejected."
            )
        revision_instructions = (
            None
            if verdict == Verdict.PASS
            else self._revision_instructions(dimensions, fresh, forced_template_id, length_note)
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
            summary=self._summary(verdict, weighted_total, fresh.fatigue, gates_relaxed),
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
                    self._judge_model(),
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

    def _judge_model(self) -> str:
        """Discrete 1-5 scoring is mechanical — route it to the light tier (Ch. — future plan 2)."""
        return select_model(self._settings, TaskTier.LIGHT, fallback=self._settings.judge_model)

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
            model=self._judge_model(),
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
        insight_ok: bool,
        fatigue: bool,
        completeness_ok: bool,
        attempt_number: int,
    ) -> Verdict:
        s = self._settings
        gate_failed = (
            (not compliance_ok)
            or grounding_score < s.grounding_min
            or not insight_ok
            or fatigue
            or not completeness_ok
        )
        if not gate_failed and weighted_total >= s.pass_threshold:
            return Verdict.PASS
        if attempt_number >= s.max_revisions:
            return Verdict.FAIL
        return Verdict.REVISE

    @staticmethod
    def _revision_instructions(dimensions, fresh, forced_template_id, length_note=None) -> str:
        """A per-dimension critique the Generator can act on — reuses the judge's own reasoning
        (justification + the evidence it flagged) for every dimension that fell short, so the
        rewrite targets the *actual* problems instead of generic advice."""
        lines: list[str] = []
        if length_note:
            lines.append(f"- {length_note}")
        if fresh.fatigue and forced_template_id:
            lines.append(
                f"- STRUCTURE: template fatigue — switch to '{forced_template_id}' and open with a "
                "noticeably different hook."
            )
        for d in dimensions:
            if d.passed and d.score >= 7.0:  # only critique what needs work
                continue
            flag = " (BELOW REQUIRED FLOOR)" if d.minimum is not None and d.score < d.minimum else ""
            why = (d.justification or "").strip()
            quote = f' Reviewer flagged: "{d.evidence.strip()}".' if d.evidence else ""
            fix = f" → {d.fix_suggestion}" if d.fix_suggestion else ""
            lines.append(f"- {d.dimension.upper()} {d.score:.1f}/10{flag}: {why}{quote}{fix}")
        return "\n".join(lines)

    @staticmethod
    def _summary(
        verdict: Verdict, weighted_total: float, fatigue: bool, gates_relaxed: bool = False
    ) -> str:
        if verdict == Verdict.PASS:
            relaxed = " · gates relaxed for a high-scoring draft" if gates_relaxed else ""
            return f"Approved for production (weighted {weighted_total}/10{relaxed})."
        tail = " Template fatigue forced a structural shift." if fatigue else ""
        return f"Verdict {verdict.value} at {weighted_total}/10.{tail}"
