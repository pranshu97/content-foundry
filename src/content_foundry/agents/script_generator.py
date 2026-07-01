"""Agent 2 — Script Generator. The single always-on LLM call (Ch. 8)."""

from __future__ import annotations

import json

from ..errors import LLMError, SchemaValidationError
from ..logging import get_logger
from ..models import DataBrief, Provenance, SceneCue, Script
from ..production.timebox import build_time_context
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model
from ..safeguards.disclosure import ensure_description_discloses
from ..safeguards.grounding import STAT_RE, ungrounded_scene_indices
from ..templates import Template

SCRIPT_JSON_SHAPE = """{
  "title_options": ["...", "..."],
  "hook": "first ~10s, specific, opens a curiosity gap",
  "scenes": [
    {"index": 0, "narration": "3-6 full spoken sentences", "on_screen_text": "caption · Source: Adzuna",
     "b_roll_keywords": ["kw1", "kw2"], "fact_ref": 0},
    {"index": 1, "narration": "3-6 full spoken sentences", "on_screen_text": "caption · Source: BLS",
     "b_roll_keywords": ["kw3"], "fact_ref": 1}
  ],
  "cta": "call to action",
  "description": "YouTube description draft (must mention synthetic content)",
  "tags": ["tag1", "tag2"],
  "thumbnail_concept": "visual idea + overlay text",
  "word_count": 0,
  "grounded_fact_refs": [0],
  "synthetic_disclosure": true
}"""


class ScriptGenerator:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="script_generator")

    def run(
        self,
        run_id: str,
        brief: DataBrief,
        template: Template,
        *,
        perspective_modifier: str = "",
        judge_feedback: str | None = None,
        attempt_number: int = 1,
    ) -> Script:
        system = self._build_prompt(brief, template, perspective_modifier, judge_feedback)
        text = self._complete(system)
        parsed = self._parse_json(system, text)
        script = self._coerce_script(parsed, run_id=run_id, template_id=template.id)
        script = self._repair_grounding(script, brief)
        script = self._ensure_min_length(system, script, brief, run_id, template.id)
        script = self._stamp_sources(script, brief)
        return script

    # ------------------------------------------------------------------ prompt
    def _build_prompt(
        self,
        brief: DataBrief,
        template: Template,
        perspective_modifier: str,
        judge_feedback: str | None,
    ) -> str:
        beats = "\n".join(f"{i + 1}) {b}" for i, b in enumerate(template.beats))
        facts = [
            {
                "index": i,
                "statement": kf.statement,
                "metric": kf.metric,
                "value": kf.value,
                "citation": {"source": kf.citation.source, "snippet": kf.citation.snippet},
            }
            for i, kf in enumerate(brief.key_facts)
        ]
        perspective = perspective_modifier or f"PERSPECTIVE: {template.default_perspective}"
        revision = (
            "REVISION — a reviewer scored your previous draft and it did NOT pass. Keep what already\n"
            "worked and rewrite to fix EVERY point below (each line is a dimension, its score, the\n"
            f"reviewer's reasoning, and the fix):\n{judge_feedback}"
            if judge_feedback
            else ""
        )
        time_context = (
            build_time_context(self._settings.effective_content_year)
            if self._settings.time_box_enabled
            else ""
        )
        return render_prompt(
            load_prompt("script_generator.system"),
            target_words=self._settings.script_target_words,
            scenes=self._settings.scenes_per_video,
            niche=brief.niche,
            template_name=template.name,
            template_beats=beats,
            perspective_modifier=perspective,
            key_facts_json=json.dumps(facts, ensure_ascii=False),
            revision_clause=revision,
            time_context=time_context,
            script_schema=SCRIPT_JSON_SHAPE,
        )

    # ------------------------------------------------------------------ llm I/O
    def _complete(self, system: str) -> str:
        resp = self._llm.complete(
            "Return ONLY the JSON script now.",
            system=system,
            temperature=self._settings.llm_temperature,
            max_tokens=self._settings.llm_max_tokens,
            model=select_model(
                self._settings, TaskTier.HEAVY, fallback=self._settings.generator_model
            ),
        )
        return resp.text

    def _parse_json(self, system: str, text: str) -> dict:
        try:
            return json.loads(extract_json(text))
        except json.JSONDecodeError:
            self._log.warning("invalid_json_reformat_retry")
        # One reformat-retry (Ch. 8.8) — a mechanical fix, so route to the light model.
        retry = self._llm.complete(
            "Your previous output was not valid JSON. Return ONLY corrected, valid JSON "
            "matching the requested shape — no prose.",
            system=system,
            temperature=0.0,
            max_tokens=self._settings.llm_max_tokens,
            model=select_model(
                self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
            ),
        )
        try:
            return json.loads(extract_json(retry.text))
        except json.JSONDecodeError as exc:
            self._log.error("script_parse_failed", snippet=retry.text[:200], error=str(exc))
            raise LLMError(f"Script generator returned invalid JSON twice: {exc}") from exc

    # --------------------------------------------------------------- coercion
    def _coerce_script(self, parsed: dict, *, run_id: str, template_id: str) -> Script:
        for managed in ("run_id", "stage", "schema_version", "template_id", "provenance"):
            parsed.pop(managed, None)

        scenes: list[SceneCue] = []
        for i, raw_scene in enumerate(parsed.get("scenes", [])):
            index = _coerce_int(raw_scene.get("index"))
            scenes.append(
                SceneCue(
                    index=i if index is None else index,
                    narration=raw_scene.get("narration", "") or "",
                    on_screen_text=raw_scene.get("on_screen_text"),
                    b_roll_keywords=raw_scene.get("b_roll_keywords", []) or [],
                    fact_ref=_coerce_int(raw_scene.get("fact_ref")),
                )
            )

        description = ensure_description_discloses(parsed.get("description", ""))
        try:
            script = Script(
                run_id=run_id,
                template_id=template_id,
                title_options=parsed.get("title_options", []) or [],
                hook=parsed.get("hook", ""),
                scenes=scenes,
                cta=parsed.get("cta", ""),
                description=description,
                tags=parsed.get("tags", []) or [],
                thumbnail_concept=parsed.get("thumbnail_concept", ""),
                word_count=0,
                grounded_fact_refs=[],
                synthetic_disclosure=True,
                provenance=Provenance(
                    produced_by="script_generator",
                    model=self._settings.generator_model,
                    config_hash=self._settings.config_hash,
                ),
            )
        except Exception as exc:  # pydantic validation
            raise SchemaValidationError(f"Generated script failed validation: {exc}") from exc
        return script

    # -------------------------------------------------------------- grounding
    def _ensure_min_length(self, system, script, brief, run_id, template_id):
        """Local models often under-produce. If a draft falls short of the Judge's completeness floor
        (``min_scenes`` / ``min_script_word_ratio`` × target), ask once more for the full-length
        script and keep whichever draft is longer — so the generator targets what the gate enforces."""
        floor = int(self._settings.min_script_word_ratio * self._settings.script_target_words)
        if len(script.scenes) >= self._settings.min_scenes and script.word_count >= floor:
            return script
        self._log.warning(
            "script_too_short", words=script.word_count, scenes=len(script.scenes)
        )
        boost = (
            f"Your previous draft was only {script.word_count} words in {len(script.scenes)} "
            f"scene(s) — far too short. Write the COMPLETE script now: about "
            f"{self._settings.script_target_words} spoken words across "
            f"{self._settings.scenes_per_video} scenes, each 3-6 full sentences. Return ONLY the JSON."
        )
        try:
            resp = self._llm.complete(
                boost, system=system, temperature=self._settings.llm_temperature,
                max_tokens=self._settings.llm_max_tokens,
                model=select_model(
                    self._settings, TaskTier.HEAVY, fallback=self._settings.generator_model
                ),
            )
            longer = self._repair_grounding(
                self._coerce_script(
                    json.loads(extract_json(resp.text)), run_id=run_id, template_id=template_id
                ),
                brief,
            )
            if longer.word_count > script.word_count:
                return longer
        except (json.JSONDecodeError, LLMError, SchemaValidationError) as exc:
            self._log.warning("length_retry_failed", error=str(exc))
        return script

    def _repair_grounding(self, script: Script, brief: DataBrief) -> Script:
        offending = set(ungrounded_scene_indices(script, brief))
        if offending:
            self._log.warning("stripping_ungrounded_stats", scenes=sorted(offending))
            for scene in script.scenes:
                if scene.index in offending:
                    scene.narration = _strip_stats(scene.narration)
        script.grounded_fact_refs = sorted(
            {
                s.fact_ref
                for s in script.scenes
                if s.fact_ref is not None and 0 <= s.fact_ref < len(brief.key_facts)
            }
        )
        script.word_count = _word_count(script)
        return script

    def _stamp_sources(self, script: Script, brief: DataBrief) -> Script:
        """HARD RULE (Ch. 8.6): a statistic is never shown without its exact source. Every scene
        whose narration still cites a (grounded) stat gets its source surfaced in on_screen_text —
        guaranteed in code, never left to the model."""
        for scene in script.scenes:
            if not STAT_RE.search(scene.narration):
                continue
            ref = scene.fact_ref
            if ref is None or not (0 <= ref < len(brief.key_facts)):
                continue  # ungrounded stats are stripped in _repair_grounding
            label = _source_label(brief.key_facts[ref].citation)
            caption = (scene.on_screen_text or "").strip()
            if _has_source(caption, label):
                continue
            scene.on_screen_text = f"{caption} · Source: {label}" if caption else f"Source: {label}"
        return script


_SOURCE_LABELS = {
    "adzuna": "Adzuna",
    "layoffs": "Layoffs.fyi",
    "news": "News",
    "bls": "U.S. BLS",
}


def _source_label(citation) -> str:
    """A short, human-readable source for the on-screen citation."""
    key = (citation.source or "").lower()
    return _SOURCE_LABELS.get(key, (citation.source or "source").title())


def _has_source(caption: str, label: str) -> bool:
    text = (caption or "").lower()
    return "source" in text or label.lower() in text


def _coerce_int(value):
    """LLMs occasionally return an int field as a list of indices ([3, 5]) or a stringified number.
    Normalise to a single int (the first usable one) or None so model validation never blows up."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        v = value.strip()
        return int(v) if v.lstrip("-").isdigit() else None
    if isinstance(value, list):
        for item in value:
            ref = _coerce_int(item)
            if ref is not None:
                return ref
    return None


def _strip_stats(text: str) -> str:
    cleaned = STAT_RE.sub("", text)
    return " ".join(cleaned.split())


def _word_count(script: Script) -> int:
    words = len(script.hook.split())
    for scene in script.scenes:
        words += len(scene.narration.split())
    return words
