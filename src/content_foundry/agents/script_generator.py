"""Agent 2 — Script Generator. The single always-on LLM call (Ch. 8)."""

from __future__ import annotations

import json
import re

from ..errors import LLMError, SchemaValidationError
from ..logging import get_logger
from ..models import DataBrief, Provenance, ResearchBrief, SceneCue, Script
from ..production.affiliate import affiliate_context, select_used
from ..production.timebox import build_time_context
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model
from ..safeguards.grounding import STAT_RE, ungrounded_scene_indices
from ..templates import Template
from .judge_checks import dedupe_scene_indices, ending_parts_present

SCRIPT_JSON_SHAPE = """{
  "title_options": ["...", "..."],
  "hook": "first ~10s spoken opening that delivers the SAME hook the title and thumbnail promise (the trifecta), specific, opens a curiosity gap",
  "scenes": [
    {"index": 0, "narration": "3-6 spoken sentences taking ONE point deep as natural speech: what to do, then the how and the why and a concrete, witty example woven together, with NO section labels or headings", "on_screen_text": "short on-screen caption",
     "b_roll_keywords": ["subject performing the main action", "close up of a key detail", "wide shot of the setting"], "fact_ref": 0, "sfx": "whoosh", "editor_note": "punch in on the key detail", "cut": "fast"},
    {"index": 1, "narration": "FINAL scene: 3-6 spoken sentences that pay off the idea with your wittiest line, then one natural like/subscribe nudge, then a warm 'see you in the next one' sign-off",
     "on_screen_text": "short on-screen caption",
     "b_roll_keywords": ["subject reacting with emotion", "two people celebrating together"], "fact_ref": 1, "sfx": "pop", "editor_note": "hold on the payoff line", "cut": "hold"}
  ],
  "cta": "call to action",
  "description": "YouTube description draft: SEO-friendly, keyword-rich first sentence, no AI/synthetic-content note",
  "tags": ["tag1", "tag2"],
  "thumbnail_concept": "ONE bold, emotional, curiosity-driving SCENE for an image generator: concrete subject + exaggerated expression + dramatic lighting + bold contrasting colors; only what the camera sees, NO words in the image",
  "thumbnail_text": "VERY short punchy overlay words for the thumbnail (2-5 words); MAY differ from the title — a bold hook or intriguing question",
  "open_loop": "the EXACT end-payoff you promised early to make viewers stay till the end, or empty string if you planted none (see the retention rules); if set it MUST be delivered in a later scene",
  "time_sensitive": false,
  "word_count": 0,
  "grounded_fact_refs": [0],
  "synthetic_disclosure": true
}"""


def _script_to_prompt_json(script: Script) -> dict:
    """Serialize a draft back into the exact JSON shape the prompt asks for, so a revision can EDIT
    its own previous draft in place instead of regenerating from scratch (which loses what worked)."""
    return {
        "title_options": script.title_options,
        "hook": script.hook,
        "scenes": [
            {
                "index": s.index,
                "narration": s.narration,
                "on_screen_text": s.on_screen_text or "",
                "b_roll_keywords": s.b_roll_keywords,
                "fact_ref": s.fact_ref,
                "sfx": s.sfx,
                "editor_note": s.editor_note,
                "cut": s.cut,
            }
            for s in script.scenes
        ],
        "cta": script.cta,
        "description": script.description,
        "tags": script.tags,
        "thumbnail_concept": script.thumbnail_concept,
        "thumbnail_text": script.thumbnail_text,
        "open_loop": script.open_loop,
        "time_sensitive": script.time_sensitive,
        "grounded_fact_refs": script.grounded_fact_refs,
        "synthetic_disclosure": script.synthetic_disclosure,
    }


def _creator_context(bio: str, title_tag: str = "") -> str:
    """Optional CREATOR CREDIBILITY clause from the user-configured ``creator_bio`` (narration
    authority) plus an optional short ``creator_title_tag`` (a credential some titles/thumbnails may
    carry). Empty when both are unset, so the shipped prompt files stay fully generic."""
    bio = (bio or "").strip()
    title_tag = (title_tag or "").strip()
    if not bio and not title_tag:
        return ""
    parts = [
        "CREATOR CREDIBILITY (use SPARINGLY, never braggy, only where it genuinely sharpens the "
        "moment):"
    ]
    if bio:
        parts.append(
            "- NARRATION: DO lean on the presenter's real background once or twice for authority — it is "
            "a genuine credibility signal that makes the advice land (this channel's edge is that the "
            f"advice comes from someone with this background). Background: {bio}. Speak it at the "
            "GENERAL level it is written ('as someone who's worked as an AI scientist in big tech', "
            "'from years inside FAANG AI teams', 'having been on the hiring side'), naturally and "
            "humbly — never a resume brag or title-drop. HARD LINE: use ONLY what that background line "
            "literally states; do NOT invent SPECIFIC, checkable details it does not give — no named "
            "project, product, system, team, metric, dollar figure, or dated event, and no 'the time I "
            "personally did X' story (e.g. from 'AI Scientist at Amazon' you MAY say 'from my time in "
            "big-tech AI' but you may NOT fabricate 'when I rebuilt Amazon's SageMaker pipeline'). The "
            "true, general credential is valuable; invented specifics are obvious fakes and a hard REJECT."
        )
    tag = (
        f'"{title_tag}"' if title_tag else
        "a SHORT, accurate credential you can infer from that background (e.g. 'FAANG AI Scientist')"
    )
    parts.append(
        "- TITLE / THUMBNAIL: on SOME (not all) of the title options, you MAY append a short "
        "credibility tag when it strengthens the hook and stays truthful — e.g. 'Resume Optimization "
        f"Tips from a FAANG AI Scientist'. Use {tag} as that tag. Keep the title under ~70 characters "
        "and never clunky; skip the tag when it doesn't fit. Never fabricate a credential."
    )
    return "\n".join(parts)


def _format_context(settings) -> str:
    """Short-form override block, injected ONLY when producing a vertical Short (empty for long-form
    so the shipped prompt stays generic). Recasts the long-form guidance into the fast, hook-first,
    caption-led pacing a ~50s vertical video needs."""
    if not getattr(settings, "is_short", False):
        return ""
    seconds = int(settings.shorts_max_duration_sec)
    return (
        "<format>\n"
        f"THIS IS A VERTICAL YOUTUBE SHORT (about {seconds} seconds, 9:16). Recast the rules above for "
        "short form:\n"
        "- ONE tight idea only: pick the single most surprising, useful point and cut everything else. "
        "No setup, no background, no 'in this video'.\n"
        f"- HARD LENGTH CAP (this OVERRIDES the 'longer is better' length rule above): the ENTIRE "
        f"script must stay UNDER about {seconds} seconds of speech — roughly "
        f"{int(settings.shorts_target_words)} words TOTAL across all {int(settings.effective_scenes)} "
        "scenes. A Short that runs long gets buried; cut hard and NEVER pad to reach a length.\n"
        "- HOOK IN THE FIRST SPOKEN LINE: open on a bold claim, a striking number, or a sharp question "
        "that stops the scroll. No greeting or throat-clearing.\n"
        "- FAST PACING: short, punchy spoken sentences. Every sentence adds a NEW beat; if it doesn't, "
        "delete it.\n"
        "- CAPTION-LED: each scene's on_screen_text is a SHORT bold caption (2-5 words) that echoes the "
        "spoken line, because most viewers watch muted.\n"
        "- Still grounded and still witty, but ONE line that lands beats three that try; stay specific.\n"
        "- Close with a quick, natural nudge to follow for more, in a single short line.\n"
        "</format>"
    )


def _retention_context(settings) -> str:
    """OPTIONAL open-loop retention nudge — LONG-FORM only (empty for a Short or when disabled). Lets
    the writer plant ONE genuine 'stick around for X' promise, with a hard anti-bait guardrail that a
    declared payoff MUST be delivered (a deterministic Judge gate enforces it)."""
    if getattr(settings, "is_short", False) or not getattr(
        settings, "retention_open_loop_enabled", True
    ):
        return ""
    return (
        "<retention_open_loop>\n"
        "OPTIONAL OPEN LOOP (a strong retention tool — YOUR call whether it fits): near the top (the "
        "hook or scene 0-1) you MAY plant ONE curiosity 'open loop' that gives the viewer a concrete "
        "reason to stay to the end — tease a SPECIFIC, valuable payoff that lands LATER (e.g. 'there is "
        "one mistake that quietly sinks most offers, and by the end I'll show you exactly how to dodge "
        "it', 'the last step is the one almost nobody does'). An unresolved question is what keeps "
        "people watching. Put the EXACT payoff you promise in the \"open_loop\" field (or leave it \"\" "
        "when you plant none).\n"
        "HARD GUARDRAILS (breaking any is a REJECT):\n"
        "- CONDITIONAL: only plant it when it fits the topic NATURALLY and genuinely helps. Never bolt "
        "on a forced, generic 'stay till the end!'. If nothing here earns a real payoff to tease, plant "
        "NONE and set open_loop to \"\" — that is the correct, common choice, and a forced tease reads "
        "worse than none.\n"
        "- NEVER A BAIT-AND-SWITCH: if you tease it, you MUST DELIVER it. The exact payoff MUST actually "
        "appear, clearly and specifically, in a LATER scene (ideally the final one before the sign-off). "
        "Teasing something and never paying it off is the single worst outcome — do not do it.\n"
        "- Tease it ONCE, in your own natural voice; do not nag about it through the script, and make "
        "the payoff feel worth the wait when it lands.\n"
        "</retention_open_loop>"
    )


_ENDING_FALLBACK_CTA = "If this helped, subscribe so the next one finds you."
_ENDING_FALLBACK_SIGNOFF = "See you in the next one."


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
        idea: str = "",
        previous_script: Script | None = None,
        research: ResearchBrief | None = None,
        affiliate_candidates: list | None = None,
    ) -> Script:
        system = self._build_prompt(
            brief, template, perspective_modifier, judge_feedback, idea, previous_script, research,
            affiliate_candidates,
        )
        text = self._complete(system)
        parsed = self._parse_json(system, text)
        script = self._coerce_script(parsed, run_id=run_id, template_id=template.id)
        script = self._dedupe_scenes(script)
        script = self._repair_grounding(script, brief)
        script = self._ensure_min_length(system, script, brief, run_id, template.id)
        script = self._prepend_intro(script)
        script = self._ensure_ending(script)
        script = self._stamp_sources(script, brief)
        script = self._stamp_affiliate(script, affiliate_candidates)
        script = self._design_sound(script)
        return script

    # ------------------------------------------------------------------ prompt
    def _build_prompt(
        self,
        brief: DataBrief,
        template: Template,
        perspective_modifier: str,
        judge_feedback: str | None,
        idea: str = "",
        previous_script: Script | None = None,
        research: ResearchBrief | None = None,
        affiliate_candidates: list | None = None,
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
        revision = self._revision_clause(judge_feedback, previous_script)
        time_context = (
            build_time_context(self._settings.effective_content_year)
            if self._settings.time_box_enabled
            else ""
        )
        creator_context = _creator_context(
            self._settings.creator_bio, self._settings.creator_title_tag
        )
        floor = int(self._settings.min_script_word_ratio * self._settings.effective_target_words)
        eff_words = self._settings.effective_target_words
        eff_scenes = self._settings.effective_scenes
        per_scene = max(
            20 if self._settings.is_short else 40, round(eff_words / max(eff_scenes, 1))
        )
        idea_focus = (
            "THIS VIDEO'S TOPIC — the single most important instruction. The whole script must "
            f'deliver EXACTLY this specific, helpful video, NOT generic "{brief.niche}" advice. Use '
            "the data only where it genuinely supports this topic:\n"
            f">>> {idea} <<<\n\n"
            if idea else ""
        )
        return render_prompt(
            load_prompt("script_generator.system"),
            target_words=eff_words,
            scenes=eff_scenes,
            min_words=floor,
            words_per_scene=per_scene,
            idea_focus=idea_focus,
            niche=brief.niche,
            template_name=template.name,
            template_beats=beats,
            perspective_modifier=perspective,
            key_facts_json=json.dumps(facts, ensure_ascii=False),
            revision_clause=revision,
            time_context=time_context,
            creator_context=creator_context,
            format_context=_format_context(self._settings),
            retention_context=_retention_context(self._settings),
            affiliate_context=affiliate_context(self._settings, candidates=affiliate_candidates),
            research_context=self._research_context(research),
            script_schema=SCRIPT_JSON_SHAPE,
        )

    def _revision_clause(self, judge_feedback: str | None, previous_script: Script | None) -> str:
        """Build the revision block. When the previous draft is available it is embedded so the model
        EDITS it in place (surgical fixes that keep what already scored well) instead of regenerating
        from scratch — the root cause of the loop dropping a good ending or wit between attempts."""
        if not judge_feedback:
            return ""
        if previous_script is not None and previous_script.scenes:
            draft = json.dumps(
                _script_to_prompt_json(previous_script), ensure_ascii=False, indent=2
            )
            return (
                "REVISION — do NOT start over. Below is YOUR PREVIOUS DRAFT; a reviewer scored it and\n"
                "it did not pass. Work FROM this draft, not a blank page.\n\n"
                f"PREVIOUS DRAFT:\n{draft}\n\n"
                "Now improve THAT draft: keep every scene, the hook, and ESPECIALLY the final scene's\n"
                "like/subscribe nudge + sign-off that already work, and change ONLY what the fix-list\n"
                "calls out. If the fix-list says the script is too short or to EXPAND, KEEP all existing\n"
                "scenes and ADD new distinct scenes plus more depth to each — never delete or shorten;\n"
                "the revised script must come back LONGER, not shorter. Return the COMPLETE revised\n"
                "script (all scenes, in order).\n\n"
                "FIX-LIST — address every point, without regressing anything already good:\n"
                f"{judge_feedback}"
            )
        return (
            "REVISION — a reviewer scored your previous draft and it did NOT pass. Keep what already\n"
            "worked and fix EVERY point below without regressing anything good (each line: a\n"
            f"dimension, its score, the reviewer's reasoning, and the fix):\n{judge_feedback}"
        )

    @staticmethod
    def _research_context(research: ResearchBrief | None) -> str:
        """Render the Researcher's depth report into a prompt section the generator mines for the
        HOW/WHY mechanism. It is background understanding, NOT a source of citable numbers — numbers
        the script SAYS still come from the GROUNDING facts (with a fact_ref), so grounding holds."""
        if not research or not research.points:
            return ""
        lines = [
            "RESEARCH (source-backed depth on THIS topic — build the video AROUND these findings and use",
            "them to EXPLAIN the mechanism, the HOW and WHY, in your own words. Their key numbers are also",
            "in your GROUNDING facts above, so when you state one, cite it with that fact's fact_ref):",
        ]
        for i, point in enumerate(research.points, 1):
            bit = f"{i}. {point.point}"
            if point.explanation:
                bit += f" | WHY/HOW: {point.explanation}"
            if point.evidence:
                bit += f" | e.g. {point.evidence}"
            lines.append(bit)
        return "\n".join(lines) + "\n"

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
                    narration=_clean_narration(raw_scene.get("narration", "") or ""),
                    on_screen_text=_replace_em_dashes(raw_scene.get("on_screen_text")),
                    b_roll_keywords=raw_scene.get("b_roll_keywords", []) or [],
                    fact_ref=_coerce_int(raw_scene.get("fact_ref")),
                    sfx=_str_or_none(raw_scene.get("sfx")),
                    editor_note=_str_or_none(raw_scene.get("editor_note")),
                    cut=_str_or_none(raw_scene.get("cut")),
                )
            )

        description = _replace_em_dashes(parsed.get("description", ""))
        try:
            script = Script(
                run_id=run_id,
                template_id=template_id,
                title_options=[
                    _replace_em_dashes(t) for t in (parsed.get("title_options", []) or [])
                ],
                hook=_clean_narration(parsed.get("hook", "") or ""),
                scenes=scenes,
                cta=_replace_em_dashes(parsed.get("cta", "")),
                description=description,
                tags=parsed.get("tags", []) or [],
                thumbnail_concept=_replace_em_dashes(parsed.get("thumbnail_concept", "")),
                thumbnail_text=_replace_em_dashes(parsed.get("thumbnail_text", "")),
                word_count=0,
                grounded_fact_refs=[],
                synthetic_disclosure=True,
                time_sensitive=bool(parsed.get("time_sensitive", False)),
                open_loop=_replace_em_dashes(parsed.get("open_loop", "") or ""),
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
        floor = int(self._settings.min_script_word_ratio * self._settings.effective_target_words)
        if len(script.scenes) >= self._settings.effective_min_scenes and script.word_count >= floor:
            return script
        self._log.warning(
            "script_too_short", words=script.word_count, scenes=len(script.scenes)
        )
        eff_words = self._settings.effective_target_words
        eff_scenes = self._settings.effective_scenes
        per_scene = max(
            20 if self._settings.is_short else 40, round(eff_words / max(eff_scenes, 1))
        )
        boost = (
            f"Your previous draft was only {script.word_count} words in {len(script.scenes)} scene(s) — "
            f"the required minimum is {floor} words. Rewrite it MUCH longer: exactly "
            f"{eff_scenes} scenes, each at least {per_scene} spoken words (4-6 full "
            f"sentences of real narration with concrete detail and examples). Total at least {floor} "
            f"words (aim for ~{eff_words}). Return ONLY the JSON."
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
                self._dedupe_scenes(
                    self._coerce_script(
                        json.loads(extract_json(resp.text)), run_id=run_id, template_id=template_id
                    )
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

    def _dedupe_scenes(self, script: Script) -> Script:
        """HARD GUARANTEE (Ch. 8.5): drop any scene whose narration near-duplicates an EARLIER scene
        (the same 3-gram Jaccard the reviewer uses), so a model that padded by recycling scenes is
        trimmed to its distinct ones BEFORE grading — a code guarantee, not a plea in the prompt. The
        length gate then EXPANDS if trimming left the draft short, forcing new content over repeats."""
        keep = dedupe_scene_indices(script, threshold=self._settings.max_scene_similarity)
        if len(keep) == len(script.scenes):
            return script
        kept_scenes = [script.scenes[i] for i in keep]
        for new_index, scene in enumerate(kept_scenes):
            scene.index = new_index
        self._log.warning(
            "deduped_scenes", dropped=len(script.scenes) - len(keep), kept=len(kept_scenes)
        )
        script.scenes = kept_scenes
        script.word_count = _word_count(script)
        return script

    def _prepend_intro(self, script: Script) -> Script:
        """Fixed channel intro (Ch. 8): every video opens with the same signature line, prepended to
        the first scene so it is the FIRST thing spoken. Guaranteed in code, topic-agnostic, and
        idempotent (never doubled — even when a revision lightly REWORDED an already-introed draft).
        A no-op when disabled or the tagline is blank."""
        if not self._settings.effective_intro_enabled or not script.scenes:
            return script
        tagline = _replace_em_dashes((self._settings.intro_tagline or "").strip())
        if not tagline:
            return script
        first = script.scenes[0]
        body = first.narration.lstrip()
        if self._opens_with_intro(body, tagline):
            return script  # already opens with the signature line (even lightly reworded)
        first.narration = f"{tagline} {body}".strip()
        script.word_count = _word_count(script)
        return script

    @staticmethod
    def _opens_with_intro(body: str, tagline: str) -> bool:
        """True when the narration already opens with the intro — including a lightly reworded or
        re-punctuated echo from a revision (e.g. 'let us' for 'let's', '.' for '!') — so the fixed
        intro is never spoken twice. Scores how many of the tagline's words land in the opening
        window rather than demanding an exact prefix."""
        tag_words = [w for w in re.findall(r"[a-z0-9]+", tagline.lower()) if len(w) >= 2]
        if not tag_words:
            return True
        window = re.findall(r"[a-z0-9]+", body.lower())[: len(tag_words) + 4]
        hits = sum(1 for w in tag_words if w in window)
        return hits >= max(2, int(0.7 * len(tag_words)))

    def _ensure_ending(self, script: Script) -> Script:
        """HARD GUARANTEE (Ch. 8): the last scene must close with BOTH a like/subscribe nudge AND a
        warm sign-off (the reviewer's ending floor). The prompt asks for it, but the model keeps
        dropping it on revisions, so if either is missing we append it here — the same 'don't rely on
        the model' pattern as source-stamping. A no-op when the model already closed properly."""
        if not script.scenes:
            return script
        has_cta, has_signoff = ending_parts_present(script)
        if has_cta and has_signoff:
            return script
        parts = []
        if not has_cta:
            parts.append(_ENDING_FALLBACK_CTA)
        if not has_signoff:
            parts.append(_ENDING_FALLBACK_SIGNOFF)
        last = script.scenes[-1]
        last.narration = (last.narration.rstrip() + " " + " ".join(parts)).strip()
        script.word_count = _word_count(script)
        self._log.info("ending_ensured", added_cta=not has_cta, added_signoff=not has_signoff)
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

    def _stamp_affiliate(self, script: Script, candidates) -> Script:
        """Persist ONLY the resolved resources the finished script actually references (name-scan of
        the narration). URLs come from the pre-resolved candidates, never the model — so a link the
        script promises is guaranteed to be in the description, and vice-versa."""
        if not candidates:
            return script
        text = " ".join(s.narration or "" for s in script.scenes)
        used = select_used(self._settings, candidates=candidates, script_text=text)
        script.affiliate_links = [
            {"label": lk.label, "url": lk.url, "blurb": lk.blurb, "mention": lk.mention}
            for lk in used
        ]
        return script

    def _design_sound(self, script: Script) -> Script:
        """Guarantee a tasteful sprinkle of sound effects when SFX is enabled — the same 'never rely
        on the model alone' guarantee used for source stamping. Local models almost always return
        sfx=null for every scene, so assign resolvable cues by scene role (opening, money, myth, data
        reveal) at ~1-in-3 spacing, leaving any the model DID author untouched."""
        if not self._settings.sfx_enabled:
            return script
        scenes = script.scenes
        n = len(scenes)
        if n == 0:
            return script
        authored = sum(1 for s in scenes if s.sfx)
        if authored >= max(2, n // 3):
            return script  # the model already designed enough sound; respect it
        last, prev, added = n - 1, "", 0
        for i, scene in enumerate(scenes):
            if scene.sfx:
                prev = scene.sfx
                continue
            text = scene.narration or ""
            strong = bool(_MONEY_RE.search(text) or _MYTH_RE.search(text))
            if not (strong or i == 0 or i == last or i % 3 == 0):
                continue
            kw = _auto_sfx(scene, is_first=(i == 0), is_last=(i == last), position=i)
            if kw == prev:  # never play the same effect twice in a row
                kw = next((c for c in _SFX_CYCLE if c != prev), kw)
            scene.sfx = kw
            prev = kw
            added += 1
        self._log.info("sound_designed", authored=authored, added=added, scenes=n)
        return script


_SOURCE_LABELS = {
    "adzuna": "Adzuna",
    "layoffs": "Layoffs.fyi",
    "news": "News",
    "bls": "U.S. BLS",
}


def _domain_from_url(url: str | None) -> str:
    """The bare site domain from a URL: 'https://online.msoe.edu/blog/x?y=1' -> 'online.msoe.edu'."""
    if not url:
        return ""
    from urllib.parse import urlparse

    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _source_label(citation) -> str:
    """A short, human-readable source for the on-screen citation. Facts without a fixed label (e.g.
    web-search results) show the site's domain from the URL — 'Source: msoe.edu' beats a generic
    'Source: Search' — falling back to the capitalised source name only when there is no URL."""
    key = (citation.source or "").lower()
    if key in _SOURCE_LABELS:
        return _SOURCE_LABELS[key]
    return _domain_from_url(getattr(citation, "url", None)) or (citation.source or "source").title()


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


def _str_or_none(value) -> str | None:
    """Accept only a non-empty string (some models return null / lists for optional cue fields)."""
    return (value.strip() or None) if isinstance(value, str) else None


# Sound-design fallback: every keyword below resolves against the default data/sounds library, so a
# script always gets audible effects even when the model authors none (see _design_sound).
_MONEY_RE = re.compile(
    r"\$|\b(?:salar(?:y|ies)|paycheck|income|compensation|wages?|bonus(?:es)?|six[- ]figure|raise)\b",
    re.IGNORECASE,
)
_MYTH_RE = re.compile(
    r"\b(?:myth|mistake|blunder|pitfall|red flag|deal ?breaker|avoid|reject|worst)\b", re.IGNORECASE
)
_SFX_CYCLE = ("whoosh", "pop", "notification", "click")


def _auto_sfx(scene, *, is_first: bool, is_last: bool, position: int) -> str:
    """Pick a resolvable sound-effect keyword for a scene from its role/content."""
    text = scene.narration or ""
    if _MONEY_RE.search(text):
        return "cash register"
    if _MYTH_RE.search(text):
        return "wrong answer"
    if is_first:
        return "whoosh"
    if scene.fact_ref is not None or STAT_RE.search(text):
        return "notification"  # a data reveal
    if is_last:
        return "pop"
    return _SFX_CYCLE[position % len(_SFX_CYCLE)]


# Structured-field annotations a model sometimes leaks INTO the spoken narration — a JSON key written
# inline, e.g. "(fact_ref: 0)" or "[b_roll: laptop]". These are never speech, so they must never be
# voiced or captioned. The prompt forbids them at the source; this is the in-code safety net.
_META_KEYS = "fact_ref|fact ref|factref|on_screen_text|b_roll_keywords|b_roll|b-roll|sfx|index"
# Bracketed form can also safely drop a leaked source/attribution — the brackets bound it, so there's
# no risk of mangling real prose (unlike a bare "Source:" whose label may contain periods).
_BRACKET_KEYS = _META_KEYS + "|sources?|according to|data from"
_META_BRACKET_RE = re.compile(
    r"\s*[(\[{]\s*(?:" + _BRACKET_KEYS + r")\b[^)\]}]*[)\]}]", re.IGNORECASE
)
_META_BARE_RE = re.compile(
    r"\s*\b(?:fact_ref|factref|on_screen_text|b_roll_keywords)\b\s*[:#=]?\s*\d*", re.IGNORECASE
)

# Textbook-style section LABELS a model sometimes announces out loud ("Why this works:", "The
# mechanism:", "Step 1:") when told to explain the how/why. They make narration sound like a read-out
# outline, so we drop the label lead-in and keep the sentence after it. Bounded to a label at the
# START of a sentence AND punctuated like a heading (a colon/period + a following word), so ordinary
# prose ("that's exactly why this works so well") is never touched. The prompt asks for natural
# phrasing; this backstops it.
_LABEL_LEADIN_RE = re.compile(
    r"(?im)(?:^|(?<=[.!?]\s))"
    r"(?:here(?:'|\u2019)?s\s+)?"
    r"(?:(?:why|how)\s+(?:this|it|that)\s+works"
    r"|the\s+(?:mechanism|reason|trick|catch|takeaway|payoff|key|point)(?:\s+(?:here|is))?"
    r"|step\s+(?:one|two|three|four|five|\d+))"
    r"\s*[:.]\s+([A-Za-z])"
)

# Company voice is a legal risk: the model must never speak AS a named company ("At Expedia Group we
# ..."). We rewrite first-person-plural that asserts affiliation with a proper-noun company to the
# third person. Bounded to the "at/with/here at <Company> ... we/our" shape so ordinary advice
# ("we've all been there", "tailor your resume") is untouched. The prompt forbids it; this backstops.
_THIRD_PERSON = {
    "we": "they", "we're": "they're", "we've": "they've", "we'll": "they'll",
    "our": "their", "ours": "theirs", "us": "them", "ourselves": "themselves",
}
_COMPANY = (
    r"[A-Z][\w&.\-]*"
    r"(?:\s+(?:[A-Z][\w&.\-]*|(?i:group|inc|llc|corp|co|ltd|labs|technologies|systems|software"
    r"|studios|holdings|ventures)))"
    r"{0,3}"
)
_AFFIL_AFTER_RE = re.compile(
    r"((?i:at|for|with|here at|join us at)\s+" + _COMPANY + r"[,:]?\s+)"
    r"((?i:we're|we've|we'll|we|ours|our|us|ourselves))\b"
)
_AFFIL_BEFORE_RE = re.compile(
    r"\b((?i:we're|we've|we'll|we))(\s+(?i:at|here at)\s+" + _COMPANY + r")"
)


def _to_third_person(pron: str) -> str:
    out = _THIRD_PERSON.get(pron.lower(), pron)
    return out[:1].upper() + out[1:] if pron[:1].isupper() else out


def _neutralize_company_voice(text: str) -> str:
    """Rewrite first-person company voice ('At Expedia Group we...') to the third person so the video
    never implies affiliation with any named company."""
    if not text:
        return text
    text = _AFFIL_AFTER_RE.sub(lambda m: m.group(1) + _to_third_person(m.group(2)), text)
    text = _AFFIL_BEFORE_RE.sub(lambda m: _to_third_person(m.group(1)) + m.group(2), text)
    return text


# HARD RULE: an em dash never appears in the script. Match the em dash and its lookalikes (the
# horizontal bar, and 2+ hyphens used as an em-dash substitute); a single hyphen in "well-known" is
# left alone. Replaced with a comma, the natural spoken/written pause, then spacing is tidied.
_EM_DASH_RE = re.compile(r"\s*(?:\u2014|\u2015|-{2,})\s*")


def _replace_em_dashes(text):
    """Replace every em dash with a comma (the appropriate substitute for a spoken pause) and tidy
    the surrounding spacing. Non-string / empty input is returned unchanged."""
    if not isinstance(text, str) or not text:
        return text
    out = _EM_DASH_RE.sub(", ", text)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)  # no space before punctuation
    out = re.sub(r",\s*(?=[,.!?;:])", "", out)  # drop a comma butting against other punctuation
    out = re.sub(r"^\s*,\s*", "", out)  # no leading comma
    return re.sub(r"\s{2,}", " ", out).strip()


# A time-sensitive script may name the year "at most once" (prompt rule). A model sometimes complies
# by DELETING the year mid-clause yet leaving the temporal preposition + comma orphaned behind: the
# hook reads "In 2026, the vast majority ..." but the scene body comes back as "In , the vast
# majority ...". Only the sentence-OPENING "<temporal preposition> ," is repaired (an opening like
# that always expected a year), so ordinary prose ("plugged in, the light came on") is never touched.
_ORPHAN_YEAR_RE = re.compile(
    r"(?i)(^|[.!?]\s+)(?:back\s+in|come|in|by|for|since|during|around|throughout|through)"
    r"(?:\s+the\s+year)?\s*,\s*(\w)"
)


def _repair_dropped_year(text: str) -> str:
    """Repair the orphan a model leaves when it drops the year but keeps the preposition and comma
    ('In , the vast majority ...' -> 'The vast majority ...'). Removes the dangling
    '<temporal preposition> ,' at a sentence start and restores the opening capital."""
    if not text or "," not in text:
        return text

    def _fix(m: re.Match) -> str:
        head, nxt = m.group(1), m.group(2)
        return f"{head}{nxt.upper()}"  # the orphan opened the sentence -> restore its capital

    return _ORPHAN_YEAR_RE.sub(_fix, text)


def _clean_narration(text: str) -> str:
    """Make narration safe to speak: strip leaked structured-field annotations, neutralize any
    first-person company voice, and remove em dashes (the prompt forbids these; this guarantees it)."""
    if not text:
        return text
    cleaned = _META_BRACKET_RE.sub("", text)
    cleaned = _META_BARE_RE.sub("", cleaned)
    cleaned = _LABEL_LEADIN_RE.sub(lambda m: m.group(1).upper(), cleaned)
    cleaned = _neutralize_company_voice(cleaned)
    cleaned = _replace_em_dashes(cleaned)
    cleaned = _repair_dropped_year(cleaned)  # fix an orphaned "In , ..." left when the year was cut
    cleaned = re.sub(r"\s+([.,!?;:])", r"\1", cleaned)  # tidy any space left before punctuation
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _strip_stats(text: str) -> str:
    cleaned = STAT_RE.sub("", text)
    return " ".join(cleaned.split())


def _word_count(script: Script) -> int:
    words = len(script.hook.split())
    for scene in script.scenes:
        words += len(scene.narration.split())
    return words
