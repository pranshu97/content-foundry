"""Agent 5.7 — Scene Image Director. When a shot gets NO relevant stock B-roll, an LLM art-director
writes a vivid, witty, richly descriptive text-to-image prompt for that beat (grounded in the scene's
narration) so the gap is filled with a bespoke, ON-TOPIC image instead of a borrowed off-topic clip.
Runs inside the visuals stage, gated by SCENE_IMAGE_DIRECTOR_ENABLED. Best-effort: any failure lets
the caller fall back to the deterministic image-prompt template.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..config import MOTION_TEMPLATE_IDS
from ..logging import get_logger
from ..production.diagram import diagram_type
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model

_MAX_AVOID = 14  # bound the context: the most recent compositions carry the most risk of repetition
_SIGNATURE_WORDS = 14  # enough to capture "An over-the-shoulder shot of a dim minimalist office"

SHOT_TYPE_INSTRUCTIONS: dict[str, str] = {
    "wide_environment": (
        "Wide establishing 24mm-35mm architectural/environmental shot showing the full physical "
        "space, depth, and natural light geometry; no macro close-ups."
    ),
    "medium_human_action": (
        "Medium 50mm candid editorial shot featuring one or two real people in frame seen from "
        "behind, in three-quarter profile, or walking/working at middle distance (faces turned "
        "away from camera and hands out of macro close-up so anatomy stays natural)."
    ),
    "tight_physical_detail": (
        "Tight 85mm-100mm macro still-life detail focused on a concrete physical object, hardware "
        "surface, instrument, or tactile artifact state with shallow depth of field and zero text."
    ),
}

SETTING_ARCHETYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "library_archive",
        re.compile(
            r"\b(?:library|libraries|archive|archives|reading\s+room|bookshelf|bookshelves|bookcase|stacks)\b",
            re.I,
        ),
    ),
    (
        "office_conference_room",
        re.compile(
            r"\b(?:conference\s+room|boardroom|briefing\s+room|meeting\s+room|empty\s+office|"
            r"corner\s+office|executive\s+office|open-plan\s+office|corporate\s+room|glass\s+atrium)\b",
            re.I,
        ),
    ),
    (
        "server_datacenter",
        re.compile(
            r"\b(?:server\s+aisle|server\s+room|data\s+center|datacenter|rack\s+bay|colocation)\b",
            re.I,
        ),
    ),
    (
        "corridor_lobby",
        re.compile(
            r"\b(?:corridor|hallway|lobby|walkway|turnstile|elevator\s+bank|foyer)\b",
            re.I,
        ),
    ),
    (
        "workshop_lab_bench",
        re.compile(
            r"\b(?:test\s+bench|hardware\s+lab|verification\s+lab|workshop|loading\s+bay|warehouse|bench)\b",
            re.I,
        ),
    ),
    (
        "exterior_campus_street",
        re.compile(
            r"\b(?:exterior|courtyard|campus|plaza|rooftop|sidewalk|street|bridge|park\s+path|facade)\b",
            re.I,
        ),
    ),
    (
        "transit_commute",
        re.compile(
            r"\b(?:train\s+platform|station|rail\s+car|ferry|terminal|parking\s+deck|crosswalk)\b",
            re.I,
        ),
    ),
    (
        "home_cafe_studio",
        re.compile(
            r"\b(?:apartment|home\s+study|kitchen\s+table|coffee\s+shop|cafe|cafeteria|lounge|studio)\b",
            re.I,
        ),
    ),
)

_HUMAN_POS_RE = re.compile(
    r"\b(?:person|people|engineer|engineers|worker|workers|colleague|colleagues|commuter|"
    r"commuters|figure|figures|silhouette|silhouetted|man|woman|pedestrian|pedestrians|"
    r"seated\s+from\s+behind|seen\s+from\s+behind|walking\s+away|three-quarter\s+profile)\b",
    re.I,
)
_HUMAN_NEG_RE = re.compile(
    r"\b(?:no\s+people|no\s+person|nobody|unoccupied|empty\s+room|depopulated|without\s+people)\b",
    re.I,
)
_PHYSICAL_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("printed text", re.compile(r"\bprinted\s+(?:text|words|lettering|numbers|figures)\b", re.I)),
    (
        "printed document",
        re.compile(
            r"\bprinted\s+(?:document|documents|page|pages|sheet|sheets|memo|ledger|report|form|contract)s?\b",
            re.I,
        ),
    ),
    (
        "document header",
        re.compile(r"\b(?:document\s+header|letterhead|typed\s+header|typed\s+page)s?\b", re.I),
    ),
    (
        "embossed seal",
        re.compile(r"\bembossed\s+(?:seal|stamp|crest|lettering|text|logo)s?\b", re.I),
    ),
    (
        "engraved/etched text",
        re.compile(
            r"\b(?:laser-etched|etched|engraved)\s+(?:plate|label|placard|nameplate|part\s+number|serial\s+number|text|lettering|words)s?\b",
            re.I,
        ),
    ),
    (
        "quoted in-scene lettering",
        re.compile(
            r"\b(?:reading|titled|labeled|labelled|stamped|inscribed|marked)\s+[\"'][^\"']{2,48}[\"']",
            re.I,
        ),
    ),
)


def classify_setting_archetype(setting: str, prompt: str = "") -> str:
    """Map a scene's setting/prompt into a canonical video-wide architectural motif bucket."""
    blob = f"{setting or ''} {prompt or ''}"
    for name, pat in SETTING_ARCHETYPE_PATTERNS:
        if pat.search(blob):
            return name
    low = (setting or "").strip().lower()
    return low[:32] if low else "other_environment"


def setting_archetype_counts(history: list[dict[str, Any]]) -> dict[str, int]:
    """Count occurrences of each canonical setting archetype across the entire video so far."""
    counts: dict[str, int] = {}
    for item in history or []:
        arch = str(item.get("setting_archetype") or "") or classify_setting_archetype(
            str(item.get("setting") or ""), str(item.get("subject") or "")
        )
        counts[arch] = counts.get(arch, 0) + 1
    return counts


def has_human_presence(prompt: str, meta: dict[str, Any] | None = None) -> bool:
    """True when a prompt/metadata depicts at least one human figure without negating people."""
    if (
        isinstance(meta, dict)
        and isinstance(meta.get("has_human"), bool)
        and meta["has_human"]
        and not _HUMAN_NEG_RE.search(prompt or "")
    ):
        return True
    txt = f"{prompt or ''} {(meta or {}).get('subject', '')}"
    if _HUMAN_NEG_RE.search(prompt or ""):
        return False
    return bool(_HUMAN_POS_RE.search(txt))


def find_physical_text_leaks(prompt: str) -> list[str]:
    """Detect prompts asking the image model to render printed/embossed/engraved/quoted lettering."""
    if not prompt:
        return []
    # Strip negative-constraint clauses ("zero legible printed text", "no printed documents") first
    # so the style bible's own bans never false-trigger the detector!
    scrubbed_neg = re.sub(
        r"\b(?:zero|no|without|forbid|never)\s+(?:legible\s+|floating\s+|visible\s+|readable\s+)*"
        r"(?:ui\s+text|printed\s+\w+|document\s+headers?|embossed\s+\w+|engraved\s+[\w\s]{1,24}|laser-etched\s+\w+)",
        " ",
        prompt,
        flags=re.I,
    )
    hits: list[str] = []
    for label, pat in _PHYSICAL_TEXT_PATTERNS:
        if pat.search(scrubbed_neg):
            hits.append(label)
    return hits


def scrub_physical_text_cues(prompt: str) -> str:
    """Deterministically rewrite physical/printed/engraved text requests into blank/closed surfaces."""
    if not prompt:
        return ""
    out = prompt
    out = re.sub(
        r"\b(?:reading|titled|labeled|labelled|stamped|inscribed|marked)\s+[\"'][^\"']{1,60}[\"']",
        "with clean unlabelled surfaces",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bprinted\s+(?:document|documents|page|pages|sheet|sheets|memo|ledger|report|form|contract)s?\b",
        "closed unlabelled archival folders",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bprinted\s+(?:text|words|lettering|numbers|figures)\b",
        "clean matte texture",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\b(?:document\s+header|letterhead|typed\s+header|typed\s+page)s?\b",
        "clean unlabelled cover",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bembossed\s+(?:seal|stamp|crest|lettering|text|logo)s?\b",
        "smooth matte finish",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\b(?:laser-etched|etched|engraved)\s+(?:plate|label|placard|nameplate|part\s+number|serial\s+number|text|lettering|words)s?\b",
        "brushed unlabelled metal surface",
        out,
        flags=re.I,
    )
    return re.sub(r"\s{2,}", " ", out).strip()


@dataclass(frozen=True)
class DualPromptResult:
    prompt: str
    visual_meta: dict[str, Any]
    redirect_to_graphic: bool = False
    suggested_template: str = ""


def build_bidirectional_window(
    all_lines: list[str],
    target_index: int,
    *,
    before: int = 2,
    after: int = 2,
) -> str:
    """Format the 5-beat bidirectional script window around ``target_index``.

    Emits ``<prev2_context>`` .. ``<prev1_context>`` .. ``<current_context>`` (explicitly marked
    as the target beat) .. ``<post1_context>`` .. ``<post2_context>`` bounded by ``before``/``after``.
    """
    if not all_lines or not (0 <= target_index < len(all_lines)):
        return "<current_context></current_context>"
    parts: list[str] = []
    for offset in range(max(0, before), 0, -1):
        idx = target_index - offset
        if 0 <= idx < len(all_lines):
            parts.append(f"<prev{offset}_context>{all_lines[idx].strip()}</prev{offset}_context>")
    parts.append(f"<current_context>{all_lines[target_index].strip()}</current_context>")
    for offset in range(1, max(0, after) + 1):
        idx = target_index + offset
        if 0 <= idx < len(all_lines):
            parts.append(f"<post{offset}_context>{all_lines[idx].strip()}</post{offset}_context>")
    return "\n".join(parts)


def format_visual_history(
    history: list[dict[str, Any]],
    *,
    window_size: int = 3,
    max_archetype_repeats: int = 2,
) -> str:
    """Render rolling ``window_size`` AI scenes PLUS video-wide exhausted setting archetypes."""
    if not history:
        return "  (none yet — first AI image scene)"
    lines: list[str] = []
    if window_size > 0:
        for item in history[-window_size:]:
            st = str(item.get("shot_type") or "unknown")
            setting = str(item.get("setting") or "unspecified")
            subj = str(item.get("subject") or "unspecified")
            objs = list(item.get("dominant_objects") or [])
            lines.append(
                f"  - shot_type={st} | setting={setting} | subject={subj} | dominant_objects={json.dumps(objs)}"
            )
    counts = setting_archetype_counts(history)
    exhausted = sorted(k for k, c in counts.items() if c >= max(1, max_archetype_repeats))
    if exhausted:
        lines.append(
            "  - EXHAUSTED VIDEO-WIDE SETTING ARCHETYPES (0 remaining budget — STRICTLY FORBIDDEN "
            f"for the rest of this video): {', '.join(exhausted)}. Choose a fresh archetype such "
            "as exterior_campus_street, transit_commute, workshop_lab_bench, corridor_lobby, or home_cafe_studio."
        )
    return "\n".join(lines) or "  (none yet)"


def find_cliche_hits(prompt: str, banlist: list[str]) -> list[str]:
    """Return every banned cliche term appearing inside ``prompt`` (case-insensitive, word-bounded)."""
    low = (prompt or "").lower()
    hits: list[str] = []
    for raw in banlist:
        term = (raw or "").strip().lower()
        if not term:
            continue
        pat = r"\b" + re.escape(term) + r"s?\b"
        if re.search(pat, low):
            hits.append(term)
    return hits


def scrub_cliche_phrases(prompt: str, banlist: list[str], *, style_bible: str = "") -> str:
    """Deterministically remove banned cliche phrases from ``prompt`` and append style bible if missing."""
    cleaned = prompt or ""
    for raw in sorted(banlist, key=len, reverse=True):
        term = (raw or "").strip()
        if not term:
            continue
        pat = re.compile(r"\b" + re.escape(term) + r"s?\b", re.IGNORECASE)
        cleaned = pat.sub("industrial enclosure", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if style_bible and "zero neon glow" not in cleaned.lower():
        cleaned = f"{cleaned.rstrip('. ')}. {style_bible.rstrip('. ')}."
    return cleaned


_PHYSICAL_ANCHORS = frozenset(
    {
        "desk",
        "table",
        "room",
        "hall",
        "rack",
        "drive",
        "badge",
        "chair",
        "door",
        "window",
        "keyboard",
        "bench",
        "notebook",
        "folder",
        "binder",
        "board",
        "whiteboard",
        "corridor",
        "lobby",
        "chassis",
        "server",
        "tray",
        "lamp",
        "paper",
        "letter",
        "pen",
        "phone",
        "headset",
        "camera",
        "clock",
        "stopwatch",
        "lab",
        "building",
    }
)


def infer_visual_meta(
    prompt: str, shot_type: str, raw_meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Extract normalized ``{subject, setting, setting_archetype, has_human, shot_type, dominant_objects}`` metadata."""
    raw = raw_meta or {}
    subj = str(raw.get("subject") or "").strip()
    setting = str(raw.get("setting") or "").strip()
    objs_raw = raw.get("dominant_objects")
    objs = (
        [str(x).strip() for x in objs_raw if str(x).strip()] if isinstance(objs_raw, list) else []
    )

    words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", prompt or "")]
    if not objs:
        found = [w for w in words if w in _PHYSICAL_ANCHORS]
        objs = list(dict.fromkeys(found))[:3] or (words[:2] if words else ["artifact"])
    if not subj:
        subj = composition_signature(prompt)[:72] or "editorial artifact"
    if not setting:
        setting = " ".join(objs[:2]) if objs else "modern workspace"
    arch = classify_setting_archetype(setting, prompt)
    human = has_human_presence(prompt, raw)
    return {
        "subject": subj,
        "setting": setting,
        "setting_archetype": arch,
        "has_human": human,
        "shot_type": shot_type or str(raw.get("shot_type") or "wide_environment"),
        "dominant_objects": objs[:4],
    }


def composition_signature(prompt: str) -> str:
    """The opening clause of a prompt \u2014 where the camera strategy, subject and setting are declared.

    That is exactly what was repeating across scenes (8 of 12 shots in one run opened with
    "over-the-shoulder..."), so it is the part later scenes must be shown in order to avoid it.
    """
    return " ".join((prompt or "").split()[:_SIGNATURE_WORDS]).strip()


class SceneImageDirector:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="scene_image_director")
        # shot index -> diagram spec, for shots the director judged better DRAWN than photographed.
        # Populated by compose(); read by the caller straight after, so the instance must be kept.
        self.diagrams: dict[int, dict] = {}

    def compose(
        self,
        *,
        shots: list[tuple[int, str]],
        narration: str = "",
        on_screen_text: str = "",
        niche: str = "",
        title: str = "",
        description: str = "",
        already_used: list[str] | None = None,
    ) -> dict[int, str]:
        """Return ``{shot_index: image_prompt}`` for the given shots — one LLM call for the whole scene.
        Empty when there are no shots or the model output is unusable (the caller then uses its
        deterministic template). Shots the model omits simply fall back individually.

        ``shots`` pairs each shot's index with the EXACT words spoken while it is on screen. That line
        is the only content signal the director gets: the script's stock-search beat used to be passed
        too, and it actively dragged images toward generic footage of the domain (a scene about pay
        bands carried the beat "corporate interview panel room", so the image showed an interview
        instead of the money). Keying by INDEX rather than by beat text also means two shots that
        happen to share a beat can never collapse onto one image.
        """
        wanted = [(i, line.strip()) for i, line in shots if line and line.strip()]
        if not wanted:
            return {}
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        used = [s for s in (already_used or []) if s.strip()][-_MAX_AVOID:]
        beats_payload = [
            {"shot": i, "spoken_while_this_shot_is_on_screen": line} for i, line in wanted
        ]
        system = render_prompt(
            load_prompt("scene_image_director.system"),
            beats_json=json.dumps(beats_payload, ensure_ascii=False, indent=2),
            narration=narration or "",
            on_screen=on_screen_text or "",
            style=self._settings.visual_style,
            niche=niche or self._settings.target_niche or "",
            # The video's own title/description are what let a generic beat hint ("person working in
            # modern office") become a shot about THIS video — the same lever that lifted the
            # thumbnail director. render_prompt is a named .replace, so these must ALWAYS be passed.
            title=title or "",
            description=description or "",
            # Each scene is its own LLM call with no memory of the others, so without this every
            # scene independently reaches for the same safe composition.
            already_used="\n".join(f"    - {s}" for s in used) or "    (nothing yet)",
        )
        resp = self._llm.complete(
            "Return ONLY the JSON now.",
            system=system,
            temperature=0.85,  # a touch of spread so shots in one scene don't converge
            max_tokens=self._settings.llm_max_tokens,
            model=model,
        )
        # The model may return a bare array or a {"shots": [...]} object; extract_json only recovers
        # objects, so try a direct parse first, then fall back to it.
        try:
            data = json.loads(resp.text.strip())
        except json.JSONDecodeError:
            data = json.loads(extract_json(resp.text))
        items = data.get("shots") if isinstance(data, dict) else data
        valid = {i for i, _ in wanted}
        out: dict[int, str] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            raw = item.get("shot")
            if raw is None:
                continue
            try:
                key = int(raw)
            except (TypeError, ValueError):
                continue
            prompt = (item.get("prompt") or "").strip()
            if key in valid and prompt:
                out[key] = prompt
                # A shot may ALSO carry a diagram spec. It rides a side channel rather than changing
                # compose()'s return type, so every existing caller is untouched — and the prompt is
                # still required, which guarantees a fallback when the render fails.
                spec = item.get("diagram")
                if diagram_type(spec) and isinstance(spec, dict):
                    self.diagrams[key] = spec
        return out

    def compose_dual(
        self,
        *,
        all_lines: list[str],
        target_index: int,
        required_shot_type: str = "wide_environment",
        visual_history: list[dict[str, Any]] | None = None,
        title: str = "",
        description: str = "",
        niche: str = "",
        critic_feedback: str = "",
    ) -> DualPromptResult | None:
        if not all_lines or not (0 <= target_index < len(all_lines)):
            return None
        target_line = all_lines[target_index].strip()
        if not target_line:
            return None

        before = int(getattr(self._settings, "script_context_window_before", 2))
        after = int(getattr(self._settings, "script_context_window_after", 2))
        hist_win = int(getattr(self._settings, "visual_history_window_size", 3))
        max_arch = int(getattr(self._settings, "max_setting_archetype_repeats", 2))
        min_human = float(getattr(self._settings, "min_human_presence_ratio", 0.35))
        style_bible = str(getattr(self._settings, "image_style_bible", "") or "").strip()
        banlist = list(getattr(self._settings, "cliche_banlist_list", []) or [])

        shot_key = (
            required_shot_type
            if required_shot_type in SHOT_TYPE_INSTRUCTIONS
            else "wide_environment"
        )
        hist_list = list(visual_history or [])
        human_so_far = sum(1 for h in hist_list if h.get("has_human"))
        need_human = shot_key == "medium_human_action" or (
            len(hist_list) >= 2 and (human_so_far / max(1, len(hist_list) + 1)) < min_human
        )
        shot_instr = SHOT_TYPE_INSTRUCTIONS[shot_key]
        if need_human and shot_key != "tight_physical_detail":
            shot_instr += (
                " MANDATORY HUMAN PRESENCE ON THIS BEAT: include 1-2 real people seen from behind, "
                "walking at middle distance, or in three-quarter silhouette (do NOT output an empty depopulated room)."
            )
        window_block = build_bidirectional_window(
            all_lines, target_index, before=before, after=after
        )
        history_block = format_visual_history(
            hist_list, window_size=hist_win, max_archetype_repeats=max_arch
        )

        system = render_prompt(
            load_prompt("scene_image_director_dual.system"),
            style_bible=style_bible or "Sharp 35mm natural-light editorial photography",
            required_shot_type=shot_key,
            shot_type_instruction=shot_instr,
            cliche_banlist=", ".join(banlist) if banlist else "(none)",
            title=title or "",
            description=description or "",
            niche=niche or getattr(self._settings, "target_niche", "") or "",
            visual_history_block=history_block,
            bidirectional_window_block=window_block,
        )
        user_msg = "Return ONLY the JSON object for <current_context> now."
        if critic_feedback:
            user_msg = (
                f"PREVIOUS ATTEMPT FAILED REVIEW: {critic_feedback}\n"
                "Fix every issue above and return ONLY the corrected JSON object now."
            )
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        resp = self._llm.complete(
            user_msg,
            system=system,
            temperature=0.75,
            max_tokens=self._settings.llm_max_tokens,
            model=model,
        )
        try:
            parsed = json.loads(resp.text.strip())
        except json.JSONDecodeError:
            parsed = json.loads(extract_json(resp.text))
        if not isinstance(parsed, dict):
            return None

        raw_prompt = str(parsed.get("prompt") or "").strip()
        if not raw_prompt:
            return None

        from .thumbnail_director import strip_career_rank

        cleaned_prompt = scrub_physical_text_cues(strip_career_rank(raw_prompt))
        redirect = bool(parsed.get("redirect_to_graphic", False))
        suggested = str(parsed.get("suggested_template") or "").strip()
        if suggested not in MOTION_TEMPLATE_IDS:
            suggested = ""

        meta = infer_visual_meta(cleaned_prompt, shot_key, parsed)
        return DualPromptResult(
            prompt=cleaned_prompt,
            visual_meta=meta,
            redirect_to_graphic=redirect,
            suggested_template=suggested,
        )


_FALLBACK_SETTINGS_POOL: tuple[tuple[str, str, list[str]], ...] = (
    (
        "exterior glass campus courtyard",
        "two engineers seen from behind walking past wet slate planters",
        ["slate planters", "campus walkway", "overcast daylight"],
    ),
    (
        "industrial hardware test bench",
        "brushed aluminum compute tray beside digital caliper",
        ["compute tray", "caliper", "steel bench"],
    ),
    (
        "quiet engineering corridor",
        "solitary engineer seen from behind walking past frosted glass panels",
        ["frosted glass", "corridor floor", "architectural light"],
    ),
    (
        "home study oak desk by rain-streaked window",
        "closed matte laptop beside ceramic mug and unlabelled notebook",
        ["closed laptop", "ceramic mug", "window light"],
    ),
    (
        "cool server aisle walkway",
        "anodized drive bay latch with amber status LED",
        ["drive bay", "anodized latch", "aisle floor"],
    ),
    (
        "commuter rail station platform at morning light",
        "commuters in winter coats seen from behind waiting beside steel canopy",
        ["platform canopy", "steel bench", "morning light"],
    ),
    (
        "daylit architectural briefing room",
        "long concrete table with closed unlabelled folders",
        ["concrete table", "closed folder", "architectural glass"],
    ),
    (
        "campus cafe mezzanine",
        "two colleagues in silhouette seated across a small round timber table",
        ["timber table", "mezzanine rail", "natural window light"],
    ),
    (
        "hardware verification lab",
        "oscilloscope chassis and unlabelled aluminum heatsink on ESD mat",
        ["heatsink", "ESD mat", "lab bench"],
    ),
    (
        "downtown pedestrian plaza outside glass tower",
        "workers seen from behind crossing damp stone pavers in cool daylight",
        ["stone pavers", "tower entrance", "morning shadow"],
    ),
)


def build_editorial_fallback_prompt(
    spoken_line: str,
    shot_type: str,
    settings: Any,
    *,
    visual_history: list[dict[str, Any]] | None = None,
) -> DualPromptResult:
    """Deterministic, cliche-free, shot-type-matched editorial prompt when no LLM is used."""
    hist_list = list(visual_history or [])
    used_settings = {str(h.get("setting") or "").strip().lower() for h in hist_list}
    arch_counts = setting_archetype_counts(hist_list)
    max_arch = int(getattr(settings, "max_setting_archetype_repeats", 2))
    min_human = float(getattr(settings, "min_human_presence_ratio", 0.35))
    shot_key = shot_type if shot_type in SHOT_TYPE_INSTRUCTIONS else "wide_environment"
    humans_so_far = sum(1 for h in hist_list if h.get("has_human"))
    below_human_floor = shot_key != "tight_physical_detail" and (
        (humans_so_far / max(1, len(hist_list) + 1)) < min_human
    )
    want_human = shot_key == "medium_human_action" or below_human_floor

    picked = _FALLBACK_SETTINGS_POOL[len(hist_list) % len(_FALLBACK_SETTINGS_POOL)]
    for candidate in _FALLBACK_SETTINGS_POOL:
        c_set, c_subj, _ = candidate
        if c_set.strip().lower() in used_settings:
            continue
        c_arch = classify_setting_archetype(c_set, c_subj)
        if arch_counts.get(c_arch, 0) >= max_arch:
            continue
        if want_human and not has_human_presence(c_subj):
            continue
        if shot_key == "tight_physical_detail" and has_human_presence(c_subj):
            continue
        picked = candidate
        break

    setting, base_subj, dom_objs = picked
    has_person = has_human_presence(base_subj)
    if want_human and not has_person:
        base_subj = f"an engineer seen from behind beside {base_subj}"
        has_person = True

    camera_lead = {
        "wide_environment": "Wide 28mm architectural establishing photograph",
        "medium_human_action": "Medium 50mm candid editorial photograph with figures seen from behind",
        "tight_physical_detail": "Tight 90mm macro physical-detail photograph with shallow depth of field",
    }[shot_key]
    style_bible = str(
        getattr(
            settings,
            "image_style_bible",
            "Sharp, natural-light 35mm editorial photography, crisp focus, zero neon glow, zero floating UI text, zero legible printed text",
        )
        or ""
    ).strip()
    banlist = list(getattr(settings, "cliche_banlist_list", []) or [])
    salient = [
        w
        for w in re.findall(r"[a-zA-Z]{4,}", spoken_line or "")
        if w.lower()
        not in {"that", "this", "with", "from", "have", "your", "they", "their", "into", "when"}
    ][:3]
    topic_anchor = f" evoking {' '.join(salient)}," if salient else ""
    people_guard = (
        "Faces turned away from camera, no close-up hands, zero legible printed text, no logos, no watermark."
        if has_person
        else "No visible faces, no hands, zero legible printed text, no logos, no watermark."
    )
    raw_prompt = (
        f"{camera_lead} at a {setting.strip()}, focusing on {base_subj},{topic_anchor} "
        f"{style_bible.rstrip('. ')}. {people_guard}"
    )
    clean_prompt = scrub_physical_text_cues(
        scrub_cliche_phrases(raw_prompt, banlist, style_bible=style_bible)
    )
    meta = {
        "subject": base_subj,
        "setting": setting.strip(),
        "setting_archetype": classify_setting_archetype(setting, clean_prompt),
        "has_human": has_person,
        "shot_type": shot_key,
        "dominant_objects": list(dom_objs),
    }
    return DualPromptResult(prompt=clean_prompt, visual_meta=meta)
