"""Deterministic 3-Tier Visual Router, Override Classifier & Anchor Spacing Controller.

Routes every visual beat across a video timeline into one of three tiers:
  1. ``programmatic_graphic`` — Priority 1 override for concrete stats, citations, trends,
     rubrics, comparisons, architectures, code/prompts, Comment Friction, the 0:00-0:08 silent
     feed preview, 0:00-0:30 opening peak beats, and periodic 45-60s mid-video anchors.
  2. ``broll`` — Priority 2 strict literal stock clip clearing ``broll_relevance_threshold``
     (and ultra-specific threshold if inside 0:08-0:30 when generic opening B-roll is banned).
  3. ``ai_image`` — Priority 3 editorial still with dual-channel context, visual exclusion
     history, and forced shot-type rotation.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ..config import MOTION_TEMPLATE_IDS
from .captions import citation_label, source_only
from .motion_graphics import clamp_text, clamp_words, validate_template_props
from .timeline import _shot_durations

VisualTier = Literal["programmatic_graphic", "broll", "ai_image"]

SHOT_TYPE_CYCLE: tuple[str, ...] = (
    "wide_environment",
    "medium_human_action",
    "tight_physical_detail",
)

HOOK_TEMPLATES: tuple[str, ...] = ("big_stat_card", "evidence_card", "split_comparison")

TEMPLATE_FAMILY_OF: dict[str, str] = {
    "split_comparison": "comparison_rubric",
    "rubric_checklist": "comparison_rubric",
    "big_stat_card": "quantitative",
    "animated_chart": "quantitative",
    "evidence_card": "evidence",
    "architecture_flow": "system_logic",
    "code_ide_mockup": "system_logic",
}

_FAKE_SOURCE_TOKENS = frozenset(
    {
        "null",
        "none",
        "n/a",
        "na",
        "unknown",
        "unspecified",
        "creator",
        "research",
        "search",
        "source",
    }
)
_QUESTION_CUE_RE = re.compile(
    r"\b(?:would\s+you|do\s+you|what\s+would|should\s+you|which\s+side|agree\s+or|"
    r"drop\s+your|let\s+me\s+know|in\s+the\s+comments)\b",
    re.IGNORECASE,
)

_STAT_TOKEN_RE = re.compile(
    r"(?:[+-]?\$\d+(?:,\d{3})*(?:\.\d+)?[KMBkmb+]?)"
    r"|(?:[+-]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|x\b|ms\b|s\b|sec\b|hrs?\b|k\b|m\b|b\b|million\b|billion\b))",
    re.IGNORECASE,
)
_PLAIN_NUM_RE = re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b")
_SPELLED_STAT_RE = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)\s+"
    r"(?:percent|times|fold|milliseconds|seconds|dollars)\b",
    re.IGNORECASE,
)
_CODE_HINT_RE = re.compile(
    r"(`[^`]+`|\b(?:python|typescript|javascript|sql|code|snippet|function|async|await|"
    r"endpoint|api\s+call|prompt\s+template|system\s+prompt|config|yaml|json|regex|"
    r"unit\s+test|tests?\s+passed|compiler|ide|pull\s+request|diff)\b)",
    re.IGNORECASE,
)
_ARCH_HINT_RE = re.compile(
    r"\b(?:architecture|pipeline|two-stage|three-stage|multi-stage|gateway|retrieval|"
    r"ranker|feature\s+store|queue|worker|cache|load\s+balancer|shard|microservice|"
    r"ingestion|orchestrator|circuit\s+breaker|data\s+flow|system\s+design)\b",
    re.IGNORECASE,
)
_RUBRIC_HINT_RE = re.compile(
    r"\b(?:rubric|strong\s+hire|lean\s+hire|no-hire|no\s+hire|downlevel|bar\s+raiser|"
    r"checklist|scorecard|debrief|criteria|pass\s+or\s+fail|red\s+flag|evaluation\s+bar)\b",
    re.IGNORECASE,
)
_COMPARE_HINT_RE = re.compile(
    r"\b(?:versus|vs\.?|compared\s+to|contrast|instead\s+of|rather\s+than|"
    r"while\s+the\s+other|on\s+one\s+hand|before\s+and\s+after|wrong\s+way|right\s+way)\b",
    re.IGNORECASE,
)
_CHART_HINT_RE = re.compile(
    r"\b(?:trend|chart|graph|curve|distribution|percentile|p50|p95|p99|year-over-year|"
    r"yoy|quarter|climbed\s+from|dropped\s+from|fell\s+from|grew\s+from|histogram)\b",
    re.IGNORECASE,
)
_CITATION_HINT_RE = re.compile(
    r"\b(?:report|survey|study|paper|benchmark|research|clearinghouse|taulbee|"
    r"bureau\s+of\s+labor|telemetry|published\s+data|dataset|audit)\b",
    re.IGNORECASE,
)
_GENERIC_BROLL_BEAT_WORDS = frozenset(
    {
        "office",
        "laptop",
        "computer",
        "desk",
        "screen",
        "monitor",
        "person",
        "people",
        "working",
        "typing",
        "modern",
        "business",
        "corporate",
        "tech",
        "technology",
        "team",
        "meeting",
        "room",
        "building",
    }
)


@dataclass
class PlannedBeat:
    global_index: int
    scene_index: int
    shot_index: int
    audio_start: float
    audio_end: float
    audio_duration: float
    viewer_start: float
    viewer_end: float
    viewer_duration: float
    spoken_text: str
    beat_query: str
    scene_narration: str = ""
    on_screen_text: str = ""
    visual_source: str = ""
    is_comment_friction: bool = False
    discussion_question: str = ""
    fact_ref: int | None = None
    visual_type: VisualTier = "ai_image"
    override_to_graphic: bool = False
    anchor_reason: str = ""
    preferred_template: str = ""
    template_id: str | None = None
    template_props: dict[str, Any] | None = None
    broll_url: str | None = None
    broll_score: float = 0.0
    shot_type: str = ""
    prompt: str = ""
    visual_meta: dict[str, Any] = field(default_factory=dict)


def is_generic_broll_query(query: str, spoken_text: str = "") -> bool:
    """True when a beat query relies only on generic office/computer set dressing."""
    words = re.findall(r"[a-z]{3,}", (query or "").lower())
    specific = [w for w in words if w not in _GENERIC_BROLL_BEAT_WORDS]
    if not specific:
        return True
    if len(specific) == 1 and spoken_text:
        spoken_words = set(re.findall(r"[a-z]{3,}", spoken_text.lower()))
        if specific[0] not in spoken_words:
            return True
    return False


def is_low_tier_source(source_or_url: str, settings: Any = None) -> bool:
    """True when ``source_or_url`` matches ``low_tier_source_domains`` (e.g. ``amazingworkplaces.co``)."""
    low = (source_or_url or "").strip().lower()
    if not low:
        return False
    domains = list(getattr(settings, "low_tier_source_domains_list", None) or ())
    if not domains:
        domains = ["amazingworkplaces.co"]
    for dom in domains:
        d = dom.strip().lower().removeprefix("www.")
        if not d:
            continue
        if low == d or low.endswith("." + d) or f"://{d}" in low or f".{d}/" in low or d in low:
            return True
    return False


def clean_verified_source(source: str | None, settings: Any = None) -> str:
    """Return a clean, non-placeholder, non-low-tier source string, or ``""`` when unverified."""
    txt = " ".join(str(source or "").split()).strip()
    if not txt or txt.lower() in _FAKE_SOURCE_TOKENS:
        return ""
    if is_low_tier_source(txt, settings):
        return ""
    return txt


def beat_supports_evidence_card(beat: PlannedBeat, settings: Any = None) -> bool:
    """``evidence_card`` is reserved strictly for beats backed by a verified source or direct quote.

    Prevents routing unverified analytical narration into quotation marks (narrator quoting itself).
    """
    src = clean_verified_source(beat.visual_source, settings)
    if not src:
        return False
    has_quote = '"' in (beat.spoken_text or "") or "\u201c" in (beat.spoken_text or "")
    has_cite = bool(_CITATION_HINT_RE.search(beat.spoken_text or ""))
    has_stat = bool(
        _STAT_TOKEN_RE.search(beat.spoken_text or "")
        or _SPELLED_STAT_RE.search(beat.spoken_text or "")
    )
    return bool(has_quote or beat.fact_ref is not None or has_cite or has_stat)


def classify_beat_override(beat: PlannedBeat, settings: Any) -> tuple[bool, str]:
    """Return ``(override_to_graphic, preferred_template_id)`` for ``beat``."""
    text = f"{beat.spoken_text} {beat.on_screen_text}".strip()
    if beat.is_comment_friction and getattr(settings, "enable_comment_friction_beat", True):
        return True, "rubric_checklist"

    stats = _STAT_TOKEN_RE.findall(text)
    spelled = _SPELLED_STAT_RE.findall(text)
    nums = _PLAIN_NUM_RE.findall(text)
    has_stat = bool(stats or spelled)
    can_ev = beat_supports_evidence_card(beat, settings)

    if _CODE_HINT_RE.search(beat.spoken_text):
        return True, "code_ide_mockup"
    if _ARCH_HINT_RE.search(beat.spoken_text):
        return True, "architecture_flow"
    if _RUBRIC_HINT_RE.search(beat.spoken_text):
        return True, "rubric_checklist"
    if _COMPARE_HINT_RE.search(beat.spoken_text):
        return True, "split_comparison"
    if len(stats) >= 2 or (has_stat and len(nums) >= 2):
        return True, "animated_chart"
    if can_ev and (
        (beat.shot_index == 0 and has_stat) or _CITATION_HINT_RE.search(beat.spoken_text)
    ):
        return True, "evidence_card"
    if has_stat:
        return True, "big_stat_card"
    return False, ""


def pick_enabled_template(
    preferred: str,
    settings: Any,
    *,
    previous_template: str | None = None,
    allowed_subset: tuple[str, ...] | None = None,
    usage_counts: dict[str, int] | None = None,
    disallowed: tuple[str, ...] = (),
    forbid_family_repeat: bool = True,
) -> str | None:
    """Choose the highest-scoring enabled template honoring ``tpl_<id>_enabled`` and ``tpl_<id>_weight``."""
    enabled_map: dict[str, tuple[float, int]] = getattr(
        settings, "enabled_motion_templates", None
    ) or dict.fromkeys(MOTION_TEMPLATE_IDS, (1.0, 120))
    if not enabled_map:
        return None

    candidates = [
        tid
        for tid in enabled_map
        if (allowed_subset is None or tid in allowed_subset) and tid not in disallowed
    ]
    if not candidates:
        candidates = [tid for tid in enabled_map if tid not in disallowed] or list(
            enabled_map.keys()
        )

    # Hard-forbid immediate repeat of the exact same template whenever another candidate exists.
    if previous_template and len(candidates) > 1:
        non_repeat = [tid for tid in candidates if tid != previous_template]
        if non_repeat:
            candidates = non_repeat

    # Forbid comparison/rubric family ping-pong (split_comparison <-> rubric_checklist) when peers exist.
    prev_family = TEMPLATE_FAMILY_OF.get(previous_template or "", "")
    if (
        forbid_family_repeat
        and prev_family == "comparison_rubric"
        and len(candidates) > 1
        and allowed_subset != ("rubric_checklist",)
    ):
        diff_family = [tid for tid in candidates if TEMPLATE_FAMILY_OF.get(tid) != prev_family]
        if diff_family:
            candidates = diff_family

    # Compatibility fallback ranking per preferred template so disabling one picks a sensible peer.
    fallbacks: dict[str, tuple[str, ...]] = {
        "big_stat_card": (
            "big_stat_card",
            "animated_chart",
            "architecture_flow",
            "split_comparison",
        ),
        "evidence_card": (
            "evidence_card",
            "big_stat_card",
            "architecture_flow",
            "split_comparison",
        ),
        "split_comparison": (
            "split_comparison",
            "architecture_flow",
            "animated_chart",
            "big_stat_card",
            "rubric_checklist",
        ),
        "rubric_checklist": (
            "rubric_checklist",
            "architecture_flow",
            "big_stat_card",
            "animated_chart",
            "split_comparison",
        ),
        "animated_chart": (
            "animated_chart",
            "big_stat_card",
            "architecture_flow",
            "split_comparison",
        ),
        "architecture_flow": (
            "architecture_flow",
            "code_ide_mockup",
            "big_stat_card",
            "split_comparison",
        ),
        "code_ide_mockup": (
            "code_ide_mockup",
            "architecture_flow",
            "big_stat_card",
            "split_comparison",
        ),
    }
    order = fallbacks.get(preferred, MOTION_TEMPLATE_IDS)
    counts = usage_counts or {}

    best_tid: str | None = None
    best_score = -1.0
    for tid in candidates:
        weight, _ = enabled_map[tid]
        if tid == preferred:
            affinity = 2.4
        elif tid in order:
            affinity = max(0.8, 1.7 - 0.25 * order.index(tid))
        else:
            affinity = 0.75
        diversity_bonus = 1.0 / (1.0 + 0.22 * counts.get(tid, 0))
        score = weight * affinity * diversity_bonus
        if score > best_score:
            best_score = score
            best_tid = tid
    return best_tid


def _nearest_complete_sentence(scene_narration: str, spoken_slice: str) -> str:
    """Resolve a word-sliced window back to the complete sentence(s) in ``scene_narration``."""
    slice_clean = " ".join((spoken_slice or "").split()).strip()
    full = " ".join((scene_narration or "").split()).strip()
    if not full or not slice_clean:
        return slice_clean or full
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", full) if s.strip()]
    if not sentences:
        return slice_clean
    slice_words = {w.lower() for w in re.findall(r"[a-zA-Z0-9%$]+", slice_clean) if len(w) >= 3}
    if not slice_words:
        return sentences[0]
    best_sent = max(
        sentences,
        key=lambda s: len(
            slice_words & {w.lower() for w in re.findall(r"[a-zA-Z0-9%$]+", s) if len(w) >= 3}
        ),
    )
    return best_sent


def build_deterministic_template_props(
    template_id: str,
    beat: PlannedBeat,
    *,
    settings: Any = None,
    video_title: str = "",
) -> dict[str, Any]:
    """Synthesize accurate, zero-hallucination ``template_props`` from ``beat`` and its full sentence."""
    max_chars = 120
    if settings is not None and hasattr(settings, "template_spec"):
        _, _, max_chars = settings.template_spec(template_id)

    raw_slice = " ".join(beat.spoken_text.split()).strip()
    # Use the beat's own clause when it already carries a full thought (>=6 words) so sibling beats
    # inside the same scene never snap back to the identical scene sentence.
    line = (
        raw_slice
        if len(raw_slice.split()) >= 6
        else (_nearest_complete_sentence(beat.scene_narration, raw_slice) or raw_slice)
    )
    clean_callout = re.sub(r"\s*·?\s*Source:.*$", "", beat.on_screen_text or "", flags=re.I).strip()
    raw_src = beat.visual_source or citation_label(beat.on_screen_text or "")
    src = clean_verified_source(raw_src, settings)

    # Unique per-beat title so multiple cards in one scene never repeat the exact same header.
    beat_headline = (
        clean_callout
        if (beat.shot_index == 0 and clean_callout)
        else clamp_words(line, 5, 52).rstrip(".,;:-").upper()
    )

    stats = _STAT_TOKEN_RE.findall(f"{line} {clean_callout}")
    all_nums_str = _PLAIN_NUM_RE.findall(f"{line} {clean_callout}")
    nums = [float(n.replace(",", "")) for n in all_nums_str[:5] if n.replace(",", "")]

    if template_id == "big_stat_card":
        if stats:
            val = stats[0].strip()
        elif all_nums_str:
            val = all_nums_str[0].strip()
        else:
            val = clamp_words(beat_headline or video_title or "KEY SIGNAL", 2, 18).upper()
        label = (
            beat_headline
            if beat_headline and beat_headline.lower() != val.lower()
            else clamp_words(line, 6, max_chars)
        )
        sublabel = clamp_text(line, max_chars) if label.lower() not in line.lower()[:30] else ""
        trend = "Measured Impact" if (stats or all_nums_str) else ""
        return validate_template_props(
            "big_stat_card",
            {
                "eyebrow": "CRITICAL SIGNAL" if beat.viewer_start < 8.0 else "VERIFIED METRIC",
                "value": val,
                "label": label or clamp_words(line, 6, max_chars),
                "sublabel": sublabel,
                "trend": trend,
                "source": src,
            },
            max_chars=max_chars,
        )

    if template_id == "evidence_card":
        pub = (src or clean_callout or "VERIFIED SOURCE BRIEF").upper()
        return validate_template_props(
            "evidence_card",
            {
                "publication": pub,
                "date_header": "PRIMARY RECORD",
                "excerpt": line or clean_callout or video_title,
                "highlight_phrase": stats[0] if stats else clamp_words(line, 5, 48),
                "source": src,
            },
            max_chars=max_chars,
        )

    if template_id == "split_comparison":
        parts = re.split(
            r"\b(?:versus|vs\.?|instead of|rather than|compared to|while|but)\b",
            line,
            maxsplit=1,
            flags=re.IGNORECASE,
        )
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            left_txt, right_txt = parts[0].strip(" ,;:-"), parts[1].strip(" ,;:-")
        else:
            clauses = [c.strip() for c in re.split(r"[.;:]|,\s+", line) if c.strip()]
            left_txt = clauses[0] if clauses else "Conventional Assumption"
            right_txt = clauses[1] if len(clauses) > 1 else (clean_callout or "Actual Mechanism")
        return validate_template_props(
            "split_comparison",
            {
                "title": beat_headline or clamp_words(video_title or "DECISION CONTRAST", 6, 64),
                "left_header": "COMMON ASSUMPTION",
                "right_header": "ACTUAL MECHANISM",
                "left_items": [left_txt],
                "right_items": [right_txt],
                "source": src,
            },
            max_chars=max_chars,
        )

    if template_id == "rubric_checklist":
        if beat.is_comment_friction or beat.discussion_question:
            return validate_template_props(
                "rubric_checklist",
                {
                    "variant": "discussion_prompt",
                    "eyebrow": "COMMENT FRICTION // DROP YOUR TAKE",
                    "claim": line,
                    "question": beat.discussion_question or "Agree or hard disagree?",
                    "options": ["Agree", "Disagree"],
                    "source": src,
                },
                max_chars=max_chars,
            )
        clauses = [c.strip() for c in re.split(r"[.;]|,\s+(?:and|but|while)\s+", line) if c.strip()]
        items = []
        for cl in clauses[:3]:
            neg = bool(
                re.search(
                    r"\b(fail|reject|downlevel|mistake|never|wrong|miss|trap|slow|cut|risk|exposed)\b",
                    cl,
                    re.I,
                )
            )
            items.append(
                {
                    "label": clamp_words(cl, 7, max_chars),
                    "detail": "",
                    "status": "AT RISK" if neg else "VERIFIED",
                    "polarity": "negative" if neg else "positive",
                }
            )
        if len(items) == 1:
            first_neg = items[0]["polarity"] == "negative"
            items.append(
                {
                    "label": clean_callout or "Surface-level default assumption",
                    "detail": "",
                    "status": "VERIFIED" if first_neg else "AT RISK",
                    "polarity": "positive" if first_neg else "negative",
                }
            )
        return validate_template_props(
            "rubric_checklist",
            {
                "variant": "checklist",
                "eyebrow": "SIGNAL CHECKLIST",
                "title": beat_headline or "KEY DECISION FACTORS",
                "items": items,
                "source": src,
            },
            max_chars=max_chars,
        )

    if template_id == "animated_chart":
        unit = (
            "%" if "%" in line else ("ms" if " ms" in line.lower() or "ms " in line.lower() else "")
        )
        if len(nums) >= 2:
            bar_items = [
                {
                    "label": f"Point {i + 1}",
                    "value": v,
                    "highlight": i == len(nums[:5]) - 1,
                }
                for i, v in enumerate(nums[:5])
            ]
            c_type = "line" if _CHART_HINT_RE.search(line) else "bar"
            if c_type == "line":
                props: dict[str, Any] = {
                    "chart_type": "line",
                    "title": clean_callout or "MEASURED TRAJECTORY",
                    "x_labels": [f"T{i + 1}" for i in range(len(nums[:5]))],
                    "series": [{"name": "Measured", "values": nums[:5]}],
                    "y_unit": unit,
                    "callout_label": "Latest",
                    "callout_value": stats[-1] if stats else f"{nums[-1]:g}{unit}",
                    "source": src,
                }
            else:
                props = {
                    "chart_type": "bar",
                    "title": clean_callout or "MAGNITUDE COMPARISON",
                    "items": bar_items,
                    "y_unit": unit,
                    "callout_label": "Key Figure",
                    "callout_value": stats[0] if stats else f"{nums[0]:g}{unit}",
                    "source": src,
                }
        elif len(nums) == 1 and unit == "%" and 0 < nums[0] < 100:
            v0 = nums[0]
            props = {
                "chart_type": "bar",
                "title": clean_callout or "SHARE BREAKDOWN",
                "items": [
                    {"label": "Cited Share", "value": v0, "highlight": True},
                    {"label": "Remainder", "value": round(100.0 - v0, 1), "highlight": False},
                ],
                "y_unit": "%",
                "callout_label": "Cited",
                "callout_value": f"{v0:g}%",
                "source": src,
            }
        else:
            v0 = nums[0] if nums else 100.0
            props = {
                "chart_type": "bar",
                "title": clean_callout or "BENCHMARK DELTA",
                "items": [
                    {
                        "label": "Baseline",
                        "value": max(1.0, round(v0 * 0.45, 1)),
                        "highlight": False,
                    },
                    {"label": "Target", "value": max(2.0, v0), "highlight": True},
                ],
                "y_unit": unit,
                "callout_label": "Signal",
                "callout_value": stats[0] if stats else f"{v0:g}{unit}",
                "source": src,
            }
        return validate_template_props("animated_chart", props, max_chars=max_chars)

    if template_id == "architecture_flow":
        raw_parts = [
            p.strip(" ,;:-")
            for p in re.split(
                r"\b(?:then|into|before|after|which feeds|and finally)\b|->|, ", line, flags=re.I
            )
            if len(p.strip()) >= 4
        ]
        if len(raw_parts) < 3:
            kw_parts = [k.strip() for k in beat.beat_query.split() if len(k.strip()) >= 3]
            raw_parts = [
                clean_callout or "Input Signal",
                clamp_words(line, 5, 36) or "Core Mechanism",
                " ".join(kw_parts[:3]).title() or "Verified Output",
            ]
        nodes = [
            {
                "tag": f"STAGE {i + 1}",
                "name": clamp_words(part, 4, 30),
                "detail": clamp_text(part, max_chars),
                "highlight": i == min(len(raw_parts), 5) - 1,
            }
            for i, part in enumerate(raw_parts[:5])
        ]
        return validate_template_props(
            "architecture_flow",
            {
                "title": clean_callout or "EXECUTION PIPELINE",
                "subtitle": clamp_words(video_title, 9, max_chars),
                "nodes": nodes,
                "source": src,
            },
            max_chars=max_chars,
        )

    # code_ide_mockup
    ident_words = [
        w
        for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", line)
        if w.lower() not in {"the", "and", "that", "this", "with", "from", "your", "when", "into"}
    ]
    fn_name = (ident_words[0].lower() if ident_words else "evaluate") + "_step"
    arg_name = ident_words[1].lower() if len(ident_words) > 1 else "payload"
    stat_lit = stats[0] if stats else "0.85"
    code_lines = [
        f"# {clamp_text(clean_callout or line, 66)}",
        f"def {fn_name}({arg_name}: Context) -> Decision:",
        f"    signal = measure_production_trace({arg_name})",
        f"    if not signal.meets_bar():  # target: {stat_lit}",
        "        return Decision.reject(reason=signal.bottleneck)",
        "    return Decision.commit(signal)",
    ]
    return validate_template_props(
        "code_ide_mockup",
        {
            "filename": f"{fn_name}.py",
            "language": "python",
            "code_lines": code_lines,
            "highlight_lines": [3, 4],
            "side_panel_title": "RUNTIME CHECK",
            "side_panel_items": [clamp_words(line, 8, 56), f"Signal: {stat_lit}"],
            "status_text": "CHECKS VERIFIED",
            "status_tone": "pass",
            "source": src,
        },
        max_chars=max_chars,
    )


def _distinct_narrative_units(narration: str) -> list[str]:
    """Break ``narration`` into distinct spoken sentences (or major clauses on very long sentences)."""
    full = " ".join((narration or "").split()).strip()
    if not full:
        return []
    raw_sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", full) if s.strip()
    ]
    if not raw_sentences:
        raw_sentences = [full]
    units: list[str] = []
    for sent in raw_sentences:
        if len(sent.split()) > 26:
            clauses = [
                c.strip()
                for c in re.split(
                    r"(?<=[;:])\s+|,\s+(?=(?:while|but|whereas|yet|instead|allowing|forcing)\b)",
                    sent,
                )
                if len(c.strip().split()) >= 5
            ]
            if len(clauses) >= 2:
                units.extend(clauses)
                continue
        units.append(sent)
    return units


def sentence_aligned_windows(narration: str, count: int, fallback_fn: Any) -> list[str]:
    """Slice ``narration`` into ``count`` windows aligned to sentence/clause boundaries first."""
    if count <= 0:
        return []
    units = _distinct_narrative_units(narration)
    if not units or count > len(units):
        return list(fallback_fn(narration, count))
    if count == len(units):
        return units
    # Group consecutive sentences proportionally by word weight so no sentence is torn in half.
    total_w = max(1, sum(len(u.split()) for u in units))
    out: list[str] = []
    idx = 0
    for slot in range(count):
        rem_slots = count - slot
        rem_units = len(units) - idx
        if rem_slots == 1 or rem_units <= 1:
            out.append(" ".join(units[idx:]).strip())
            idx = len(units)
            break
        target_w = total_w / count
        bucket: list[str] = []
        cur_w = 0
        while idx < len(units) and (len(units) - idx) >= rem_slots:
            u = units[idx]
            uw = len(u.split())
            if bucket and cur_w + uw > target_w * 1.25:
                break
            bucket.append(u)
            cur_w += uw
            idx += 1
            if (len(units) - idx) < (rem_slots - 1):
                break
        if not bucket and idx < len(units):
            bucket.append(units[idx])
            idx += 1
        out.append(" ".join(bucket).strip())
    while len(out) < count:
        out.append(units[-1])
    return out[:count]


def _pick_comment_friction_shot_index(windows: list[str], discussion_question: str = "") -> int:
    """Return the shot index within a scene whose spoken slice actually asks the friction question."""
    if not windows:
        return 0
    q_words = {
        w.lower()
        for w in re.findall(r"[a-zA-Z]{4,}", discussion_question or "")
        if w.lower() not in {"this", "that", "your", "with", "from", "have", "what", "would"}
    }
    best_j = -1
    best_score = 0.0
    for j, win_text in enumerate(windows):
        txt = win_text or ""
        score = 0.0
        if "?" in txt:
            score += 4.0
        if _QUESTION_CUE_RE.search(txt):
            score += 2.5
        if q_words:
            w_set = {w.lower() for w in re.findall(r"[a-zA-Z]{4,}", txt)}
            score += 1.2 * len(q_words & w_set)
        # Ties break toward the LATER window where the question lands at the end of the scene.
        if score > 0.0 and score >= best_score:
            best_score = score
            best_j = j
    return best_j if best_j >= 0 else (len(windows) - 1 if len(windows) > 1 else 0)


def build_planned_beats(
    script: Any,
    voiceover: Any,
    settings: Any,
    *,
    narration_windows_fn: Any,
    cut_pace_fn: Any,
) -> list[PlannedBeat]:
    """Slice ``script.scenes`` into viewer-clock-aligned ``PlannedBeat`` records."""
    speed = max(0.25, float(getattr(settings, "video_speed", 1.0) or 1.0))
    open_peak = float(getattr(settings, "opening_peak_window_sec", 30.0))
    open_min = float(getattr(settings, "opening_beat_min_sec", 3.0))
    open_max = float(getattr(settings, "opening_beat_max_sec", 5.0))
    open_mid = max(1.5, 0.5 * (open_min + open_max))
    max_tpl_dur = max(2.5, float(getattr(settings, "max_template_duration_sec", 8.0) or 8.0))
    prog_on = bool(getattr(settings, "enable_programmatic_graphics", True))

    timing_by_scene = {
        st.scene_index: st for st in (getattr(voiceover, "scene_timings", None) or [])
    }
    word_timings = list(getattr(voiceover, "word_timings", None) or [])

    planned: list[PlannedBeat] = []
    cursor_fallback = 0.0
    g_idx = 0

    for scene in sorted(script.scenes, key=lambda s: s.index):
        st = timing_by_scene.get(scene.index)
        if st is not None:
            a_start, a_end = float(st.start), float(st.end)
        else:
            est_dur = max(3.0, len((scene.narration or "").split()) / 2.5)
            a_start, a_end = cursor_fallback, cursor_fallback + est_dur
            cursor_fallback = a_end

        a_dur = max(0.2, a_end - a_start)
        v_start = a_start / speed
        v_end = a_end / speed
        v_dur = max(0.2, v_end - v_start)

        raw_beats = [k.strip() for k in (scene.b_roll_keywords or []) if k and k.strip()]
        units = _distinct_narrative_units(scene.narration or "")
        hold_floor = (
            math.ceil(v_dur / max_tpl_dur)
            if (prog_on and not getattr(settings, "is_short", False))
            else 1
        )

        if v_start < open_peak and not getattr(settings, "is_short", False):
            # Opening peak window: brisk cuts capped by distinct narrative units so we never chop
            # one sentence into duplicate comparison cards, while still honoring max_template_duration_sec.
            in_peak = max(0.0, min(v_end, open_peak) - v_start)
            past_peak = max(0.0, v_end - max(v_start, open_peak))
            desired_peak = round(in_peak / max(open_mid, 4.5))
            tail_shots = round(past_peak / max(max_tpl_dur, 7.5)) if past_peak > 2.0 else 0
            unit_cap = max(len(units), hold_floor, 1)
            n_shots = max(hold_floor, min(12, unit_cap, max(1, desired_peak + tail_shots)))
        else:
            pace = cut_pace_fn(getattr(scene, "cut", None))
            pace_cap = max(1, int(a_dur // (2.0 * pace)) or 1)
            n_shots = max(
                1,
                min(max(len(raw_beats), hold_floor, 1), pace_cap, 20),
            )

        windows = sentence_aligned_windows(scene.narration or "", n_shots, narration_windows_fn)
        # Pad or trim beat search queries so every window has a corresponding beat hint.
        padded_queries: list[str] = []
        for j in range(n_shots):
            if j < len(raw_beats):
                padded_queries.append(raw_beats[j])
            elif raw_beats:
                padded_queries.append(raw_beats[j % len(raw_beats)])
            else:
                padded_queries.append(clamp_words(windows[j], 4, 48))

        dummy_shots = [type("_S", (), {"duration_sec": a_dur / n_shots})() for _ in range(n_shots)]
        spoken_starts = [w.start for w in word_timings if a_start <= w.start < a_end]
        beat_a_durs = _shot_durations(dummy_shots, spoken_starts, a_start, a_end)

        raw_vsrc = (getattr(scene, "visual_source", None) or "").strip() or source_only(
            scene.on_screen_text or ""
        ).replace("Source:", "").strip()
        vis_src = clean_verified_source(raw_vsrc, settings)
        is_cf = bool(getattr(scene, "is_comment_friction", False))
        disc_q = (getattr(scene, "discussion_question", None) or "").strip()
        cf_shot_idx = _pick_comment_friction_shot_index(windows, disc_q) if is_cf else -1

        # Sub-split any individual beat whose viewer hold exceeds max_template_duration_sec so no
        # motion graphic ever freezes across two long spoken beats.
        cur_a = a_start
        shot_counter = 0
        for j in range(n_shots):
            bd_a = float(beat_a_durs[j])
            bd_v = bd_a / speed
            sub_count = (
                math.ceil(bd_v / max_tpl_dur)
                if (prog_on and bd_v > max_tpl_dur + 0.35 and len(windows[j].split()) >= 8)
                else 1
            )
            sub_windows = (
                sentence_aligned_windows(windows[j], sub_count, narration_windows_fn)
                if sub_count > 1
                else [windows[j]]
            )
            sub_cf_idx = (
                _pick_comment_friction_shot_index(sub_windows, disc_q)
                if (is_cf and j == cf_shot_idx and sub_count > 1)
                else 0
            )
            sub_dur_a = bd_a / sub_count
            for s_i, sub_txt in enumerate(sub_windows):
                nxt_a = (
                    cur_a + sub_dur_a if s_i < sub_count - 1 else (cur_a + bd_a - s_i * sub_dur_a)
                )
                actual_da = max(0.05, nxt_a - cur_a)
                beat_is_cf = bool(is_cf and j == cf_shot_idx and s_i == sub_cf_idx)
                planned.append(
                    PlannedBeat(
                        global_index=g_idx,
                        scene_index=scene.index,
                        shot_index=shot_counter,
                        audio_start=round(cur_a, 4),
                        audio_end=round(nxt_a, 4),
                        audio_duration=round(actual_da, 4),
                        viewer_start=round(cur_a / speed, 4),
                        scene_narration=scene.narration or "",
                        viewer_end=round(nxt_a / speed, 4),
                        viewer_duration=round(actual_da / speed, 4),
                        spoken_text=sub_txt,
                        beat_query=padded_queries[j]
                        if s_i == 0
                        else clamp_words(sub_txt, 4, 48) or padded_queries[j],
                        on_screen_text=scene.on_screen_text or "",
                        visual_source=vis_src,
                        is_comment_friction=beat_is_cf,
                        discussion_question=disc_q if beat_is_cf else "",
                        fact_ref=scene.fact_ref,
                    )
                )
                cur_a = nxt_a
                shot_counter += 1
                g_idx += 1

    return planned


def route_visual_storyboard(
    beats: list[PlannedBeat],
    settings: Any,
    *,
    broll_Lookup_fn: Any = None,
    video_title: str = "",
) -> list[PlannedBeat]:
    """Deterministically route ``beats`` across the 3 visual tiers while enforcing:
    1. 0:00-SILENT_PREVIEW_WINDOW_SEC high-contrast hook graphic on beat 0 (and whole preview if banned).
    2. 0:00-OPENING_PEAK_WINDOW_SEC zero Ken Burns AI stills / zero generic B-roll (per flags).
    3. Priority 1 ``override_to_graphic`` for data, citations, comparisons, code, flows, rubrics.
    4. Periodic programmatic anchors every ``programmatic_anchor_min_sec..max_sec`` (no 0:31 cliff).
    5. Programmatic share balanced within ``[min_programmatic_ratio, max_programmatic_ratio]``
       around ``target_programmatic_ratio``.
    6. Forced shot-type rotation across consecutive ``ai_image`` beats.
    """
    if not beats:
        return beats

    prog_enabled = bool(getattr(settings, "enable_programmatic_graphics", True)) and bool(
        getattr(settings, "enabled_motion_templates", True)
    )
    silent_win = float(getattr(settings, "silent_preview_window_sec", 8.0))
    open_win = float(getattr(settings, "opening_peak_window_sec", 30.0))
    ban_kb_open = bool(getattr(settings, "ban_ken_burns_in_opening", True))
    ban_gen_broll_open = bool(getattr(settings, "ban_generic_broll_in_opening", True))
    anchor_min = float(getattr(settings, "programmatic_anchor_min_sec", 45.0))
    anchor_max = float(getattr(settings, "programmatic_anchor_max_sec", 60.0))
    min_ratio = float(getattr(settings, "min_programmatic_ratio", 0.20))
    target_ratio = float(getattr(settings, "target_programmatic_ratio", 0.30))
    max_ratio = float(getattr(settings, "max_programmatic_ratio", 0.45))

    # Pass 1: evaluate B-roll candidates & raw override flags per beat.
    for b in beats:
        ov, pref = classify_beat_override(b, settings)
        b.override_to_graphic = ov and prog_enabled
        b.preferred_template = pref
        if broll_Lookup_fn is not None:
            in_opening = b.viewer_start < open_win
            if (
                in_opening
                and ban_gen_broll_open
                and is_generic_broll_query(b.beat_query, b.spoken_text)
            ):
                b.broll_url = None
                b.broll_score = 0.0
            else:
                res = broll_Lookup_fn(b, ultra_strict=in_opening)
                if isinstance(res, tuple):
                    b.broll_url, b.broll_score = res
                else:
                    b.broll_url = res
                    b.broll_score = 0.85 if res else 0.0

    if not prog_enabled:
        for b in beats:
            b.visual_type = "broll" if b.broll_url else "ai_image"
        _assign_shot_types(beats, settings)
        return beats

    n = len(beats)
    locked_graphic: set[int] = set()

    # Pass 2: Hard-lock Silent Inline Preview (0:00-0:08) and Opening Peak Window (0:00-0:30).
    for idx, b in enumerate(beats):
        if idx == 0 or b.viewer_start < silent_win:
            # Beat 0 MUST open on a high-contrast hook graphic. Subsequent beats inside 0:00-0:08
            # also default to programmatic graphic so muted preview never shows panning AI or fluff.
            b.visual_type = "programmatic_graphic"
            b.anchor_reason = "silent_preview"
            if not b.preferred_template or b.preferred_template not in HOOK_TEMPLATES:
                b.preferred_template = (
                    "big_stat_card"
                    if _STAT_TOKEN_RE.search(b.spoken_text)
                    else (
                        "split_comparison"
                        if _COMPARE_HINT_RE.search(b.spoken_text)
                        else "big_stat_card"
                    )
                )
            locked_graphic.add(idx)
        elif b.viewer_start < open_win:
            # Inside 0:08-0:30: if override_to_graphic OR (no valid specific B-roll and Ken Burns is banned),
            # must be a programmatic graphic! If ultra-specific B-roll qualified and not overridden, allow B-roll
            # so opening mixes graphics + crisp specific footage without inflating ratio needlessly.
            if b.override_to_graphic:
                b.visual_type = "programmatic_graphic"
                b.anchor_reason = "opening_peak"
            elif b.broll_url:
                b.visual_type = "broll"
            elif ban_kb_open:
                b.visual_type = "programmatic_graphic"
                b.anchor_reason = "opening_peak"
                b.preferred_template = b.preferred_template or "split_comparison"
                locked_graphic.add(idx)
            else:
                b.visual_type = "ai_image"
        else:
            if b.override_to_graphic:
                b.visual_type = "programmatic_graphic"
                b.anchor_reason = "comment_friction" if b.is_comment_friction else "data_override"
                if b.is_comment_friction:
                    locked_graphic.add(idx)
            elif b.broll_url:
                b.visual_type = "broll"
            else:
                b.visual_type = "ai_image"

    # Pass 3: Enforce periodic anchors every 45-60s across the ENTIRE timeline (no 0:31 cliff).
    def _ensure_periodic_anchors() -> None:
        last_anchor_end = 0.0
        for idx, b in enumerate(beats):
            if b.visual_type == "programmatic_graphic":
                last_anchor_end = b.viewer_end
                continue
            gap = b.viewer_end - last_anchor_end
            if gap >= anchor_min:
                # Promote this beat (or look ahead within anchor_max for a natural override/non-broll beat).
                best_i = idx
                for probe_i in range(idx, n):
                    if beats[probe_i].viewer_start - last_anchor_end > anchor_max:
                        break
                    if beats[probe_i].visual_type == "programmatic_graphic":
                        best_i = -1
                        last_anchor_end = beats[probe_i].viewer_end
                        break
                    if beats[probe_i].override_to_graphic or not beats[probe_i].broll_url:
                        best_i = probe_i
                        break
                if best_i >= 0:
                    target_b = beats[best_i]
                    target_b.visual_type = "programmatic_graphic"
                    target_b.anchor_reason = target_b.anchor_reason or "periodic_anchor"
                    default_anchor_tpl = (
                        "evidence_card"
                        if beat_supports_evidence_card(target_b, settings)
                        else "architecture_flow"
                    )
                    target_b.preferred_template = target_b.preferred_template or default_anchor_tpl
                    locked_graphic.add(best_i)
                    last_anchor_end = target_b.viewer_end

    _ensure_periodic_anchors()

    # Helper to test if demoting graphic index `cand_idx` would violate anchor_max or opening bans.
    def _can_demote(cand_idx: int) -> bool:
        if cand_idx in locked_graphic:
            return False
        cb = beats[cand_idx]
        if cb.viewer_start < open_win and ban_kb_open and not cb.broll_url:
            return False
        # Check max gap between remaining programmatic graphics if `cand_idx` is demoted.
        prev_end = 0.0
        for k, kb in enumerate(beats):
            is_g = (k != cand_idx) and (kb.visual_type == "programmatic_graphic")
            if is_g:
                if kb.viewer_start - prev_end > anchor_max:
                    return False
                prev_end = kb.viewer_end
        total_end = beats[-1].viewer_end
        return total_end - prev_end <= anchor_max

    # Pass 4: Balance programmatic ratio toward target_programmatic_ratio within [min_ratio, max_ratio].
    def _prog_count() -> int:
        return sum(1 for b in beats if b.visual_type == "programmatic_graphic")

    min_count = max(1, math.ceil(min_ratio * n))
    max_count = max(min_count, math.floor(max_ratio * n))
    target_count = max(min_count, min(max_count, round(target_ratio * n)))

    # If above max_count (e.g. a very stat-dense script), demote lowest-priority discretionary overrides
    # that have B-roll or safe post-opening AI fallback without breaking periodic anchors.
    if _prog_count() > max_count:
        demotable = [
            idx
            for idx in range(n - 1, 0, -1)
            if beats[idx].visual_type == "programmatic_graphic" and _can_demote(idx)
        ]
        # Prefer demoting beats that already have strong B-roll first, then non-stat overrides.
        demotable.sort(
            key=lambda i: (
                0 if beats[i].broll_url else 1,
                0 if beats[i].viewer_start >= open_win else 1,
                -i,
            )
        )
        for idx in demotable:
            if _prog_count() <= max_count:
                break
            if _can_demote(idx):
                beats[idx].visual_type = "broll" if beats[idx].broll_url else "ai_image"
                beats[idx].anchor_reason = ""

    # If below target_count (or min_count), promote best non-B-roll / high-signal beats spaced evenly.
    if _prog_count() < target_count:
        promotable = [idx for idx, b in enumerate(beats) if b.visual_type != "programmatic_graphic"]
        # Prefer promoting AI-image gap beats over literal B-roll clips so good footage is kept.
        promotable.sort(
            key=lambda i: (
                0 if beats[i].visual_type == "ai_image" else 1,
                0 if beats[i].viewer_start < open_win else 1,
                i,
            )
        )
        for idx in promotable:
            if _prog_count() >= target_count:
                break
            beats[idx].visual_type = "programmatic_graphic"
            beats[idx].anchor_reason = beats[idx].anchor_reason or "ratio_fill"
            beats[idx].preferred_template = beats[idx].preferred_template or "split_comparison"

    # Pass 5: Assign concrete enabled templates + validated props to all programmatic_graphic beats.
    prev_tpl: str | None = None
    usage: dict[str, int] = {}
    for idx, b in enumerate(beats):
        if b.visual_type != "programmatic_graphic":
            b.template_id = None
            b.template_props = None
            continue
        can_ev = beat_supports_evidence_card(b, settings)
        disallowed = () if can_ev else ("evidence_card",)
        subset = HOOK_TEMPLATES if idx == 0 else None
        if b.is_comment_friction:
            subset = ("rubric_checklist",)
            disallowed = ()
        pref = b.preferred_template or "big_stat_card"
        if pref == "evidence_card" and not can_ev:
            pref = (
                "big_stat_card"
                if _STAT_TOKEN_RE.search(b.spoken_text or "")
                else "architecture_flow"
            )
        tid = pick_enabled_template(
            pref,
            settings,
            previous_template=prev_tpl,
            allowed_subset=subset,
            usage_counts=usage,
            disallowed=disallowed,
        )
        if tid is None:
            b.visual_type = "broll" if b.broll_url else "ai_image"
            continue
        b.template_id = tid
        b.template_props = build_deterministic_template_props(
            tid, b, settings=settings, video_title=video_title
        )
        usage[tid] = usage.get(tid, 0) + 1
        prev_tpl = tid

    _assign_shot_types(beats, settings)
    return beats


def _assign_shot_types(beats: list[PlannedBeat], settings: Any) -> None:
    """Rotate camera framing across consecutive AI-image beats."""
    rotate = bool(getattr(settings, "enforce_shot_type_rotation", True))
    ai_counter = 0
    for b in beats:
        if b.visual_type == "ai_image":
            b.shot_type = (
                SHOT_TYPE_CYCLE[ai_counter % len(SHOT_TYPE_CYCLE)] if rotate else "wide_environment"
            )
            ai_counter += 1
        else:
            b.shot_type = ""


def author_motion_graphics_props(
    beats: list[PlannedBeat],
    settings: Any,
    llm_provider: Any,
    *,
    video_title: str = "",
    niche: str = "",
    skip_indices: set[int] | None = None,
) -> int:
    """Populate crisp, complete, executive-grade ``template_props`` via batched LIGHT-tier LLM calls.

    Falls back cleanly to ``build_deterministic_template_props`` on any beat omitted or if ``llm_provider``
    is None / fails, and validates every output dict through ``validate_template_props``.
    Returns the number of beats enriched by the LLM.
    """
    if llm_provider is None:
        return 0
    skip = skip_indices or set()
    targets = [
        b
        for b in beats
        if b.visual_type == "programmatic_graphic" and b.template_id and b.global_index not in skip
    ]
    if not targets:
        return 0

    from ..prompts import load_prompt, render_prompt
    from ..providers.base import extract_json
    from ..providers.tiering import TaskTier, select_model

    model = select_model(settings, TaskTier.LIGHT, fallback=settings.generator_model)
    by_global = {b.global_index: b for b in targets}
    enriched = 0

    # Batch in groups of <=14 beats so output stays well inside LLM_MAX_TOKENS even with thinking on.
    batch_size = 14
    for start in range(0, len(targets), batch_size):
        chunk = targets[start : start + batch_size]
        payload = [
            {
                "global_index": b.global_index,
                "template_id": b.template_id,
                "spoken_text": b.spoken_text,
                "scene_narration": b.scene_narration,
                "visual_source": b.visual_source,
                "is_comment_friction": b.is_comment_friction,
                "discussion_question": b.discussion_question,
            }
            for b in chunk
        ]
        try:
            system = render_prompt(
                load_prompt("motion_graphics_director.system"),
                title=video_title or "",
                niche=niche or getattr(settings, "target_niche", "") or "",
                beats_json=json.dumps(payload, ensure_ascii=False, indent=2),
            )
            resp = llm_provider.complete(
                "Return ONLY the JSON object for these graphics beats now.",
                system=system,
                temperature=0.35,
                max_tokens=settings.llm_max_tokens,
                model=model,
            )
            try:
                parsed = json.loads(resp.text.strip())
            except json.JSONDecodeError:
                parsed = json.loads(extract_json(resp.text))
            items = parsed.get("graphics") if isinstance(parsed, dict) else parsed
            if not isinstance(items, list):
                continue
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                g_id = entry.get("global_index")
                raw_props = entry.get("props")
                if (
                    not isinstance(g_id, int)
                    or g_id not in by_global
                    or not isinstance(raw_props, dict)
                ):
                    continue
                target_b = by_global[g_id]
                tid = target_b.template_id
                if not tid:
                    continue
                # Preserve mandatory comment-friction variant & question if flagged.
                if target_b.is_comment_friction and tid == "rubric_checklist":
                    raw_props = {
                        **raw_props,
                        "variant": "discussion_prompt",
                        "question": raw_props.get("question") or target_b.discussion_question,
                    }
                # Deterministic provenance lock: `source` may ONLY show a verified beat source,
                # never an LLM-invented report/matrix name on analytical beats.
                verified_src = clean_verified_source(target_b.visual_source, settings)
                raw_props = {**raw_props, "source": verified_src}
                max_c = 120
                if hasattr(settings, "template_spec"):
                    _, _, max_c = settings.template_spec(tid)
                target_b.template_props = validate_template_props(tid, raw_props, max_chars=max_c)
                enriched += 1
        except Exception:
            continue

    return enriched
