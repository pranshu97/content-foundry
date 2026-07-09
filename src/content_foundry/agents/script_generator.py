"""Agent 2 — Script Generator. The single always-on LLM call (Ch. 8)."""

from __future__ import annotations

import json
import re

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
    {"index": 0, "narration": "3-6 full spoken sentences", "on_screen_text": "short on-screen caption",
     "b_roll_keywords": ["developer typing code", "team standup meeting", "code on monitor"], "fact_ref": 0, "sfx": "whoosh"},
    {"index": 1, "narration": "FINAL scene: 3-6 spoken sentences that pay off the idea with your wittiest line, then one natural like/subscribe nudge, then a warm 'see you in the next one' sign-off",
     "on_screen_text": "short on-screen caption",
     "b_roll_keywords": ["recruiter reading resume", "job interview handshake"], "fact_ref": 1, "sfx": "pop"}
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
        idea: str = "",
    ) -> Script:
        system = self._build_prompt(brief, template, perspective_modifier, judge_feedback, idea)
        text = self._complete(system)
        parsed = self._parse_json(system, text)
        script = self._coerce_script(parsed, run_id=run_id, template_id=template.id)
        script = self._repair_grounding(script, brief)
        script = self._ensure_min_length(system, script, brief, run_id, template.id)
        script = self._stamp_sources(script, brief)
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
        floor = int(self._settings.min_script_word_ratio * self._settings.script_target_words)
        per_scene = max(
            40, round(self._settings.script_target_words / max(self._settings.scenes_per_video, 1))
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
            target_words=self._settings.script_target_words,
            scenes=self._settings.scenes_per_video,
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
                    narration=_clean_narration(raw_scene.get("narration", "") or ""),
                    on_screen_text=_replace_em_dashes(raw_scene.get("on_screen_text")),
                    b_roll_keywords=raw_scene.get("b_roll_keywords", []) or [],
                    fact_ref=_coerce_int(raw_scene.get("fact_ref")),
                    sfx=_str_or_none(raw_scene.get("sfx")),
                )
            )

        description = _replace_em_dashes(
            ensure_description_discloses(parsed.get("description", ""))
        )
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
        per_scene = max(
            40, round(self._settings.script_target_words / max(self._settings.scenes_per_video, 1))
        )
        boost = (
            f"Your previous draft was only {script.word_count} words in {len(script.scenes)} scene(s) — "
            f"the required minimum is {floor} words. Rewrite it MUCH longer: exactly "
            f"{self._settings.scenes_per_video} scenes, each at least {per_scene} spoken words (4-6 full "
            f"sentences of real narration with concrete detail and examples). Total at least {floor} "
            f"words (aim for ~{self._settings.script_target_words}). Return ONLY the JSON."
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


def _clean_narration(text: str) -> str:
    """Make narration safe to speak: strip leaked structured-field annotations, neutralize any
    first-person company voice, and remove em dashes (the prompt forbids these; this guarantees it)."""
    if not text:
        return text
    cleaned = _META_BRACKET_RE.sub("", text)
    cleaned = _META_BARE_RE.sub("", cleaned)
    cleaned = _neutralize_company_voice(cleaned)
    cleaned = _replace_em_dashes(cleaned)
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
