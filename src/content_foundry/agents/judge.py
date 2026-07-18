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
    ending_report,
    freshness_and_fatigue,
    freshness_why,
    heuristic_actionability,
    heuristic_engagement,
    heuristic_insight,
    heuristic_wittiness,
    hook_score,
    hook_why,
    redundancy_report,
    specificity_score,
    specificity_why,
)

# Dimension weights (Ch. 9.3) sum to 1.0, so weighted_total is a plain weighted average on 0-5.
WEIGHTS = {
    "actionability": 0.14,
    "specificity": 0.14,
    "grounding": 0.14,
    "insight": 0.14,
    "engagement": 0.10,
    "wittiness": 0.07,
    "ending": 0.07,
    "hook": 0.10,
    "freshness": 0.07,
    "compliance": 0.03,
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
        # Every score in this judge is on a 0-5 scale (matching the LLM's native 1-5). The
        # deterministic checks below compute on 0-10, so they are HALVED (``/ 2``) as they enter the
        # rubric; the LLM dims are used as their raw 1-5 integer.
        grounding = check_grounding(script, brief)
        g5 = round(grounding.score / 2, 2)  # grounding on the 0-5 rubric scale
        comp_score, comp_ok = compliance_check(script)
        spec = specificity_score(script)
        hk = hook_score(script)
        fresh = freshness_and_fatigue(
            script.template_id, script.hook, recent_template_ids, recent_hooks
        )
        end, end_detail = ending_report(script)
        end5 = round(end / 2, 2)  # ending on the 0-5 rubric scale
        redundancy_ok, redundancy_note = redundancy_report(script, threshold=s.max_scene_similarity)

        # An egregiously short draft (a single scene) is rejected without spending an LLM call,
        # exactly like a grounding/compliance violation. Full completeness (scene/word floors) is
        # evaluated after the weighted total, so a high-scoring draft can earn a little slack.
        hard_gate_failed = (
            (not comp_ok)
            or (g5 < s.grounding_min)
            or (len(script.scenes) < 2)
            or (not redundancy_ok)
        )

        # ---- subjective dims: LLM (hybrid/llm) or heuristic (deterministic / fallback) ----
        subj, score_1_5, evidence, justif, used_model = self._subjective_scores(
            script, hard_gate_failed
        )
        act, ins = subj["actionability"], subj["insight"]
        eng, wit = subj["engagement"], subj["wittiness"]
        # Build per-dimension justification strings for all code-scored dims so the revision note
        # tells the generator WHY each dimension fell short, not just the raw number.
        code_justif = {
            "specificity": specificity_why(script),
            "grounding": f"grounding scored {g5}/5 (floor {s.grounding_min}); "
                         f"floor {'met' if g5 >= s.grounding_min else 'NOT met'}. "
                         "Tie every stated number to a fact_ref from the DataBrief.",
            "hook": hook_why(script),
            "freshness": freshness_why(script.template_id, fresh, recent_template_ids),
            "compliance": "synthetic_disclosure flag is not set.",
            "ending": end_detail,
        }
        justif = {**justif, **code_justif}

        # ---- forced shift target on fatigue (deterministic) ----
        forced_template_id = None
        if fresh.fatigue:
            forced_template_id = select_template(
                recent_template_ids, exclude=script.template_id, rng=random.Random(0)
            ).id

        # ---- assemble dimension scores ----
        raw = {
            "actionability": act,
            "specificity": round(spec / 2, 2),
            "grounding": g5,
            "insight": ins,
            "engagement": eng,
            "wittiness": wit,
            "ending": end5,
            "hook": round(hk / 2, 2),
            "freshness": round(fresh.score / 2, 2),
            "compliance": round(comp_score / 2, 2),
        }
        floors = {
            "grounding": s.grounding_min,
            "insight": s.insight_min,
            "wittiness": s.wittiness_min,
            "ending": s.ending_min,
        }
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
        strict_floor = int(s.min_script_word_ratio * s.effective_target_words)
        relief = s.gate_relief_ratio if weighted_total >= s.gate_relief_score else 0.0
        factor = 1.0 - relief
        word_floor = int(strict_floor * factor)
        min_scenes_eff = max(2, round(s.effective_min_scenes * factor))
        completeness_ok = (
            len(script.scenes) >= min_scenes_eff and script.word_count >= word_floor
        )
        insight_ok = ins >= s.insight_min * factor
        wittiness_ok = wit >= s.wittiness_min * factor
        ending_ok = end5 >= s.ending_min
        strict_complete = (
            len(script.scenes) >= s.effective_min_scenes and script.word_count >= strict_floor
        )
        gates_relaxed = relief > 0.0 and (
            (completeness_ok and not strict_complete)
            or (insight_ok and ins < s.insight_min)
            or (wittiness_ok and wit < s.wittiness_min)
        )

        verdict = self._verdict(
            weighted_total=weighted_total,
            compliance_ok=comp_ok,
            grounding_score=g5,
            insight_ok=insight_ok,
            wittiness_ok=wittiness_ok,
            ending_ok=ending_ok,
            redundancy_ok=redundancy_ok,
            fatigue=fresh.fatigue,
            completeness_ok=completeness_ok,
            attempt_number=attempt_number,
        )

        length_note = None
        if not completeness_ok:
            shortfall = max(0, word_floor - script.word_count)
            per_scene = max(
                20 if s.is_short else 40,
                round(s.effective_target_words / max(s.effective_scenes, 1)),
            )
            length_note = (
                f"LENGTH — HARD FAIL (this is why it did not pass): your draft is only "
                f"{script.word_count} words in {len(script.scenes)} scene(s); the REQUIRED minimum is "
                f"{word_floor} words (target ~{s.effective_target_words}). You are ~{shortfall} words short. "
                f"Do NOT delete anything — EXPAND: write {s.effective_scenes} scenes, each at least "
                f"{per_scene} words (4-6 full sentences), adding concrete detail, examples, and the data "
                f"to every scene. Any script under {word_floor} words is automatically rejected."
            )
        revision_instructions = (
            None
            if verdict == Verdict.PASS
            else self._revision_instructions(
                dimensions, fresh, forced_template_id, length_note,
                None if redundancy_ok else redundancy_note,
            )
        )

        return JudgeReport(
            run_id=run_id,
            attempt_number=attempt_number,
            template_id=script.template_id,
            scores=dimensions,
            weighted_total=weighted_total,
            insight_score=ins,
            grounding_score=g5,
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
    _SUBJECTIVE = ("actionability", "insight", "engagement", "wittiness")

    def _subjective_scores(self, script: Script, hard_gate_failed: bool):
        """Return (scores, score_1_5, evidence, justification, used_model) for the four subjective
        dimensions — LLM-scored in hybrid/llm mode, heuristic in deterministic mode or on any LLM
        failure. Engagement and wittiness carry NO hard floor; they only shape the weighted total."""
        mode = self._settings.judge_mode
        want_llm = mode in ("hybrid", "llm") and (mode == "llm" or not hard_gate_failed)

        if want_llm and self._llm is not None:
            try:
                data = self._llm_scores(script)
                scores, s15, evidence, justif = {}, {}, {}, {}
                for name in self._SUBJECTIVE:
                    d = data.get(name) or {}
                    # The LLM's 1-5 integer IS the score on our 0-5 scale (no conversion). Floors are
                    # 1-5 too, so e.g. insight_min=3.5 means "a genuine 4 clears it, a 3 does not".
                    n = max(1, min(5, int(d.get("score_1_5", 3))))
                    scores[name] = float(n)
                    s15[name] = n
                    evidence[name] = d.get("evidence")
                    justif[name] = d.get("justification", "LLM-scored")
                return scores, s15, evidence, justif, self._judge_model()
            except (LLMError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self._log.warning("judge_llm_fallback", error=str(exc))

        # deterministic / fallback heuristics
        return (
            {
                # heuristics compute on 0-10; halve to the 0-5 rubric scale.
                "actionability": round(heuristic_actionability(script) / 2, 2),
                "insight": round(heuristic_insight(script) / 2, 2),
                "engagement": round(heuristic_engagement(script) / 2, 2),
                "wittiness": round(heuristic_wittiness(script) / 2, 2),
            },
            {},
            {},
            dict.fromkeys(self._SUBJECTIVE, "heuristic"),
            None,
        )

    def _judge_model(self) -> str:
        """Discrete 1-5 scoring is mechanical — route it to the light tier (Ch. — future plan 2)."""
        return select_model(self._settings, TaskTier.LIGHT, fallback=self._settings.judge_model)

    def _llm_scores(self, script: Script) -> dict[str, dict]:
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
        return {
            name: (data.get(name) if isinstance(data.get(name), dict) else {})
            for name in self._SUBJECTIVE
        }

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
        fix = None if (passed and score >= 3.5) else self._fix_for(name, forced_template_id)
        return DimensionScore(
            dimension=name,
            score_1_5=score_1_5,
            score=round(score, 2),
            weight=WEIGHTS[name],
            minimum=floor,
            passed=passed,
            evidence=evidence,
            justification=justification or f"{name} scored {round(score, 2)}/5.",
            fix_suggestion=fix,
        )

    @staticmethod
    def _fix_for(name: str, forced_template_id: str | None) -> str:
        return {
            "actionability": "Give 2-3 concrete, specific steps the viewer can do this week.",
            "specificity": "Name concrete numbers, roles, and tools from the data.",
            "grounding": "Tie every number to a DataBrief fact_ref; remove invented stats.",
            "insight": "Add a non-obvious, data-backed reframing; cut clichés.",
            "engagement": "Hook harder and hold it: open a curiosity loop, raise the stakes, vary the pace, talk TO the viewer.",
            "wittiness": "Dial up the VOICE, don't bolt on filler jokes: rewrite 2-3 flat lines with a comedic device — a vivid analogy, a silly exaggerated example (e.g. 'less processing power than a 2005 toaster'), a rule-of-three with a sharp third turn, a callback to an earlier line, or naming the obvious elephant in the room. A genuinely funny voice all the way through beats one forced quip; keep every number exact.",
            "ending": "End the last scene with BOTH a like/subscribe nudge AND a warm sign-off (e.g. 'subscribe for more data-backed moves', then 'see you in the next one'), on top of a witty payoff line.",
            "hook": "Open with a specific number or claim in the first ~10 seconds.",
            "freshness": f"Switch structure to {forced_template_id or 'a different template'}; vary the hook.",
            "compliance": "Set synthetic_disclosure=true.",
        }.get(name, "Improve this dimension.")

    def _verdict(
        self,
        *,
        weighted_total: float,
        compliance_ok: bool,
        grounding_score: float,
        insight_ok: bool,
        wittiness_ok: bool,
        ending_ok: bool,
        redundancy_ok: bool,
        fatigue: bool,
        completeness_ok: bool,
        attempt_number: int,
    ) -> Verdict:
        s = self._settings
        gate_failed = (
            (not compliance_ok)
            or grounding_score < s.grounding_min
            or not insight_ok
            or not wittiness_ok
            or not ending_ok
            or not redundancy_ok
            or fatigue
            or not completeness_ok
        )
        if not gate_failed and weighted_total >= s.pass_threshold:
            return Verdict.PASS
        if attempt_number >= s.max_revisions:
            return Verdict.FAIL
        return Verdict.REVISE

    @staticmethod
    def _revision_instructions(
        dimensions, fresh, forced_template_id, length_note=None, redundancy_note=None
    ) -> str:
        """A per-dimension critique the Generator can act on — reuses the judge's own reasoning
        (justification + the evidence it flagged) for every dimension that fell short, so the
        rewrite targets the *actual* problems instead of generic advice."""
        lines: list[str] = []
        strengths = [d.dimension for d in dimensions if d.passed and d.score >= 3.5]
        if strengths:
            lines.append(
                "- KEEP INTACT (already strong — edit around these, do NOT let them regress): "
                f"{', '.join(strengths)}."
            )
        if redundancy_note:
            lines.append(f"- {redundancy_note}")
        if length_note:
            lines.append(f"- {length_note}")
        if fresh.fatigue and forced_template_id:
            lines.append(
                f"- STRUCTURE: template fatigue — switch to '{forced_template_id}' and open with a "
                "noticeably different hook."
            )
        for d in dimensions:
            if d.passed and d.score >= 3.5:  # only critique what needs work
                continue
            flag = " (BELOW REQUIRED FLOOR)" if d.minimum is not None and d.score < d.minimum else ""
            why = (d.justification or "").strip()
            quote = f' Reviewer flagged: "{d.evidence.strip()}".' if d.evidence else ""
            fix = f" → {d.fix_suggestion}" if d.fix_suggestion else ""
            lines.append(f"- {d.dimension.upper()} {d.score:.1f}/5{flag}: {why}{quote}{fix}")
        return "\n".join(lines)

    @staticmethod
    def _summary(
        verdict: Verdict, weighted_total: float, fatigue: bool, gates_relaxed: bool = False
    ) -> str:
        if verdict == Verdict.PASS:
            relaxed = " · gates relaxed for a high-scoring draft" if gates_relaxed else ""
            return f"Approved for production (weighted {weighted_total}/5{relaxed})."
        tail = " Template fatigue forced a structural shift." if fatigue else ""
        return f"Verdict {verdict.value} at {weighted_total}/5.{tail}"
