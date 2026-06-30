"""Agent 2 — Script Generator. The single always-on LLM call (Ch. 8)."""

from __future__ import annotations

import json

from ..errors import LLMError, SchemaValidationError
from ..logging import get_logger
from ..models import DataBrief, Provenance, SceneCue, Script
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..safeguards.disclosure import ensure_description_discloses
from ..safeguards.grounding import STAT_RE, ungrounded_scene_indices
from ..templates import Template

SCRIPT_JSON_SHAPE = """{
  "title_options": ["...", "..."],
  "hook": "first ~10s, specific, opens a curiosity gap",
  "scenes": [
    {"index": 0, "narration": "spoken words", "on_screen_text": "caption",
     "b_roll_keywords": ["kw1", "kw2"], "fact_ref": 0}
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
            f"REVISION INSTRUCTIONS (address all of this): {judge_feedback}"
            if judge_feedback
            else ""
        )
        return render_prompt(
            load_prompt("script_generator.system"),
            target_words=self._settings.script_target_words,
            niche=brief.niche,
            template_name=template.name,
            template_beats=beats,
            perspective_modifier=perspective,
            key_facts_json=json.dumps(facts, ensure_ascii=False),
            revision_clause=revision,
            script_schema=SCRIPT_JSON_SHAPE,
        )

    # ------------------------------------------------------------------ llm I/O
    def _complete(self, system: str) -> str:
        resp = self._llm.complete(
            "Return ONLY the JSON script now.",
            system=system,
            temperature=self._settings.llm_temperature,
            max_tokens=self._settings.llm_max_tokens,
            model=self._settings.generator_model,
        )
        return resp.text

    def _parse_json(self, system: str, text: str) -> dict:
        try:
            return json.loads(extract_json(text))
        except json.JSONDecodeError:
            self._log.warning("invalid_json_reformat_retry")
        # One reformat-retry (Ch. 8.8).
        retry = self._llm.complete(
            "Your previous output was not valid JSON. Return ONLY corrected, valid JSON "
            "matching the requested shape — no prose.",
            system=system,
            temperature=0.0,
            max_tokens=self._settings.llm_max_tokens,
            model=self._settings.generator_model,
        )
        try:
            return json.loads(extract_json(retry.text))
        except json.JSONDecodeError as exc:
            raise LLMError(f"Script generator returned invalid JSON twice: {exc}") from exc

    # --------------------------------------------------------------- coercion
    def _coerce_script(self, parsed: dict, *, run_id: str, template_id: str) -> Script:
        for managed in ("run_id", "stage", "schema_version", "template_id", "provenance"):
            parsed.pop(managed, None)

        scenes: list[SceneCue] = []
        for i, raw_scene in enumerate(parsed.get("scenes", [])):
            scenes.append(
                SceneCue(
                    index=raw_scene.get("index", i),
                    narration=raw_scene.get("narration", ""),
                    on_screen_text=raw_scene.get("on_screen_text"),
                    b_roll_keywords=raw_scene.get("b_roll_keywords", []) or [],
                    fact_ref=raw_scene.get("fact_ref"),
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


def _strip_stats(text: str) -> str:
    cleaned = STAT_RE.sub("", text)
    return " ".join(cleaned.split())


def _word_count(script: Script) -> int:
    words = len(script.hook.split())
    for scene in script.scenes:
        words += len(scene.narration.split())
    return words
