"""Workstream 5 — Pre-Render Storyboard Critic & Auto-Repair Gate.

Runs immediately after storyboard planning and strictly BEFORE paid AI image generation or
video rendering. Combines fast, deterministic pure-code validators (0:00-0:08 silent preview,
0:00-0:30 opening peak bans, programmatic ratio bounds, 45-60s anchor intervals, template prop
limits, cliche banlist, shot-type rotation, visual_history non-repetition) with a targeted LLM
audit and selective per-shot regeneration up to ``critic_max_retries``.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..logging import get_logger
from ..production.motion_graphics import validate_template_props
from ..production.visual_router import (
    HOOK_TEMPLATES,
    SHOT_TYPE_CYCLE,
    TEMPLATE_FAMILY_OF,
    PlannedBeat,
    beat_supports_evidence_card,
    build_deterministic_template_props,
    clean_verified_source,
    is_generic_broll_query,
    is_low_tier_source,
    pick_enabled_template,
)
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.broll import has_broll_cliche
from ..providers.tiering import TaskTier, select_model
from .scene_image_director import (
    DualPromptResult,
    build_editorial_fallback_prompt,
    classify_setting_archetype,
    find_cliche_hits,
    find_physical_text_leaks,
    has_human_presence,
    scrub_cliche_phrases,
    scrub_physical_text_cues,
)

_OPENING_FLUFF_RE = re.compile(
    r"^\s*(?:hey\s+guys|welcome\s+back|what\s+is\s+up|in\s+today'?s\s+video|hello\s+everyone)\b",
    re.IGNORECASE,
)


@dataclass
class StoryboardValidationReport:
    ok: bool
    timeline_issues: list[str] = field(default_factory=list)
    shot_issues: dict[int, list[str]] = field(default_factory=dict)
    repairs_applied: list[str] = field(default_factory=list)
    retries_used: int = 0


def _props_exceed_max_chars(props: dict[str, Any] | None, max_chars: int) -> list[str]:
    if not isinstance(props, dict):
        return ["missing_props"]
    exceeded: list[str] = []
    for k, v in props.items():
        if k in ("code_lines", "files", "series", "x_labels", "highlight_lines", "options"):
            continue
        if isinstance(v, str) and len(v) > max_chars + 4:
            exceeded.append(k)
        elif isinstance(v, list):
            for idx, item in enumerate(v):
                if isinstance(item, str) and len(item) > max_chars + 4:
                    exceeded.append(f"{k}[{idx}]")
                elif isinstance(item, dict):
                    for sk, sv in item.items():
                        if isinstance(sv, str) and len(sv) > max_chars + 4:
                            exceeded.append(f"{k}[{idx}].{sk}")
    return exceeded


def validate_storyboard_deterministic(
    beats: list[PlannedBeat], settings: Any
) -> tuple[list[str], dict[int, list[str]]]:
    """Pure deterministic validator over a planned storyboard.

    Returns ``(timeline_issues, shot_issues_by_global_index)``.
    """
    timeline_issues: list[str] = []
    shot_issues: dict[int, list[str]] = {}

    if not beats:
        return timeline_issues, shot_issues

    def _flag_shot(g_idx: int, msg: str) -> None:
        shot_issues.setdefault(g_idx, []).append(msg)

    prog_enabled = bool(getattr(settings, "enable_programmatic_graphics", True)) and bool(
        getattr(settings, "enabled_motion_templates", True)
    )
    silent_win = float(getattr(settings, "silent_preview_window_sec", 8.0))
    open_win = float(getattr(settings, "opening_peak_window_sec", 30.0))
    ban_kb_open = bool(getattr(settings, "ban_ken_burns_in_opening", True))
    ban_gen_broll_open = bool(getattr(settings, "ban_generic_broll_in_opening", True))
    min_ratio = float(getattr(settings, "min_programmatic_ratio", 0.20))
    max_ratio = float(getattr(settings, "max_programmatic_ratio", 0.45))
    anchor_max = float(getattr(settings, "programmatic_anchor_max_sec", 60.0))
    broll_thresh = float(getattr(settings, "broll_relevance_threshold", 0.78))
    banlist = list(getattr(settings, "cliche_banlist_list", []) or [])
    rotate = bool(getattr(settings, "enforce_shot_type_rotation", True))
    hist_win = int(getattr(settings, "visual_history_window_size", 3))

    # 1. Silent Inline Preview (0:00 - 0:08)
    b0 = beats[0]
    if prog_enabled and b0.visual_type != "programmatic_graphic":
        timeline_issues.append("0:00-0:08 beat 0 must open on a high-contrast programmatic_graphic")
        _flag_shot(b0.global_index, "opening beat 0 is not programmatic_graphic")
    if _OPENING_FLUFF_RE.search(b0.spoken_text or ""):
        timeline_issues.append("0:00-0:08 opening beat contains greeting/intro fluff")
        _flag_shot(b0.global_index, "opening greeting fluff detected")

    for b in beats:
        if (
            b.viewer_start < silent_win
            and prog_enabled
            and ban_kb_open
            and b.visual_type == "ai_image"
        ):
            timeline_issues.append(
                f"beat {b.global_index} at {b.viewer_start:.1f}s uses panning ai_image inside silent preview"
            )
            _flag_shot(b.global_index, "ai_image forbidden in silent preview window")

    # 2. Peak Hook Window (0:00 - 0:30)
    for b in beats:
        if b.viewer_start < open_win:
            if ban_kb_open and b.visual_type == "ai_image":
                timeline_issues.append(
                    f"beat {b.global_index} at {b.viewer_start:.1f}s violates ban_ken_burns_in_opening"
                )
                _flag_shot(b.global_index, "ken_burns ai_image banned in opening peak window")
            if (
                ban_gen_broll_open
                and b.visual_type == "broll"
                and (
                    is_generic_broll_query(b.beat_query, b.spoken_text)
                    or b.broll_score < broll_thresh
                )
            ):
                timeline_issues.append(
                    f"beat {b.global_index} at {b.viewer_start:.1f}s uses generic/weak broll in opening"
                )
                _flag_shot(b.global_index, "generic or low-relevance broll banned in opening")

    # 3. Programmatic Ratio & Periodic Anchor Interval (45-60s)
    if prog_enabled:
        n = len(beats)
        prog_count = sum(1 for b in beats if b.visual_type == "programmatic_graphic")
        min_allowed = max(1, math.ceil(min_ratio * n)) if n >= 5 else 1
        # Mandatory opening + anchor floor on short synthetic timelines cannot be faulted if
        # 0:00-0:30 has no B-roll and ban_ken_burns_in_opening is True.
        mandatory_open = sum(
            1 for b in beats if b.viewer_start < open_win and ban_kb_open and not b.broll_url
        )
        mandatory_cf = sum(1 for b in beats if b.viewer_start >= open_win and b.is_comment_friction)
        mandatory_anchors = max(
            mandatory_cf,
            math.floor(max(0.0, beats[-1].viewer_end - open_win) / anchor_max),
        )
        max_allowed = max(math.ceil(max_ratio * n), mandatory_open + mandatory_anchors)
        if prog_count < min_allowed:
            timeline_issues.append(
                f"programmatic count {prog_count}/{n} ({prog_count / n:.2f}) below min_programmatic_ratio {min_ratio:.2f}"
            )
        if prog_count > max_allowed:
            timeline_issues.append(
                f"programmatic count {prog_count}/{n} ({prog_count / n:.2f}) exceeds max_programmatic_ratio {max_ratio:.2f}"
            )

        last_graphic_end = 0.0
        for b in beats:
            if b.visual_type == "programmatic_graphic":
                if b.viewer_start - last_graphic_end > anchor_max + 0.5:
                    timeline_issues.append(
                        f"anchor gap {b.viewer_start - last_graphic_end:.1f}s before beat {b.global_index} exceeds {anchor_max:.1f}s"
                    )
                last_graphic_end = b.viewer_end
        tail_gap = beats[-1].viewer_end - last_graphic_end
        if tail_gap > anchor_max + 0.5:
            timeline_issues.append(
                f"tail anchor gap {tail_gap:.1f}s after {last_graphic_end:.1f}s exceeds {anchor_max:.1f}s"
            )

    # 4. Template Props Schema, max_chars, Unverified Source Join & Evidence-Card Guard
    enabled_map = getattr(settings, "enabled_motion_templates", {})
    max_tpl_dur = float(getattr(settings, "max_template_duration_sec", 8.0) or 8.0)
    prev_graphic_tpl: str | None = None
    for b in beats:
        if b.visual_type == "broll":
            broll_bans = [
                *banlist,
                *list(getattr(settings, "broll_style_banlist_list", ()) or ()),
            ]
            if has_broll_cliche(b.beat_query, b.broll_url or "", broll_bans):
                _flag_shot(b.global_index, f"broll cliche violation in query/url: {b.beat_query!r}")
            continue
        if b.visual_type != "programmatic_graphic":
            continue
        if not b.template_id or (enabled_map and b.template_id not in enabled_map):
            _flag_shot(b.global_index, f"invalid or disabled template_id={b.template_id!r}")
            continue
        if len(beats) >= 8 and b.viewer_duration > max_tpl_dur + 0.75:
            timeline_issues.append(
                f"beat {b.global_index} template hold {b.viewer_duration:.1f}s exceeds max_template_duration_sec {max_tpl_dur:.1f}s"
            )
        if prev_graphic_tpl and b.template_id == prev_graphic_tpl and len(enabled_map) > 1:
            _flag_shot(b.global_index, f"back-to-back duplicate template_id={b.template_id!r}")
        prev_graphic_tpl = b.template_id

        _, _, max_c = settings.template_spec(b.template_id)
        over = _props_exceed_max_chars(b.template_props, max_c)
        if over:
            _flag_shot(b.global_index, f"template_props exceed max_chars ({max_c}): {over}")

        prop_src = str((b.template_props or {}).get("source") or "").strip()
        verified_b_src = clean_verified_source(b.visual_source, settings)
        if prop_src and is_low_tier_source(prop_src, settings):
            _flag_shot(b.global_index, f"low-tier source forbidden in SOURCE badge: {prop_src!r}")
        elif prop_src and verified_b_src and prop_src.lower() != verified_b_src.lower():
            # Allow only when the beat actually carries a verified visual_source matching it.
            if (
                verified_b_src.lower() not in prop_src.lower()
                and prop_src.lower() not in verified_b_src.lower()
            ):
                _flag_shot(b.global_index, f"unverified hallucinated source badge: {prop_src!r}")
        elif prop_src and not verified_b_src and len(beats) >= 6:
            _flag_shot(
                b.global_index, f"unverified source badge without beat provenance: {prop_src!r}"
            )

        if b.template_id == "evidence_card" and not beat_supports_evidence_card(b, settings):
            _flag_shot(b.global_index, "evidence_card used without verified source/quote")

    # 5 & 6. AI Image Cliche Banlist, Physical Text Leakage, Video-Wide Setting Cap, Human Ratio & Rotation
    max_arch_repeats = int(getattr(settings, "max_setting_archetype_repeats", 2))
    min_human_ratio = float(getattr(settings, "min_human_presence_ratio", 0.35))
    ai_history: list[dict[str, Any]] = []
    arch_counts: dict[str, int] = {}
    ai_beats_list = [b for b in beats if b.visual_type == "ai_image"]
    human_hits = 0
    for ai_idx, b in enumerate(ai_beats_list):
        if not (b.prompt or "").strip():
            _flag_shot(b.global_index, "empty ai_image prompt")
        hits = find_cliche_hits(b.prompt, banlist)
        if hits:
            _flag_shot(b.global_index, f"cliche_banlist violation: {', '.join(hits)}")
        phys_text_hits = find_physical_text_leaks(b.prompt)
        if phys_text_hits:
            _flag_shot(
                b.global_index,
                f"physical_text_leak violation: {', '.join(phys_text_hits)}",
            )

        expected_shot = SHOT_TYPE_CYCLE[ai_idx % len(SHOT_TYPE_CYCLE)] if rotate else b.shot_type
        if rotate and b.shot_type != expected_shot:
            _flag_shot(
                b.global_index,
                f"shot_type {b.shot_type!r} breaks rotation (expected {expected_shot!r})",
            )

        meta = b.visual_meta or {}
        if has_human_presence(b.prompt, meta):
            human_hits += 1
        if not meta.get("subject") or not meta.get("setting"):
            _flag_shot(b.global_index, "missing structured visual_meta (subject/setting)")
        else:
            cur_setting = str(meta.get("setting") or "").strip().lower()
            cur_subj = str(meta.get("subject") or "").strip().lower()
            arch = str(meta.get("setting_archetype") or "") or classify_setting_archetype(
                cur_setting, b.prompt
            )
            arch_counts[arch] = arch_counts.get(arch, 0) + 1
            if arch_counts[arch] > max_arch_repeats:
                _flag_shot(
                    b.global_index,
                    f"exceeds video-wide setting archetype cap ({arch!r} count={arch_counts[arch]} > {max_arch_repeats})",
                )
            if hist_win > 0 and ai_history:
                recent = ai_history[-hist_win:]
                for prev in recent:
                    if (
                        cur_setting
                        and cur_setting == str(prev.get("setting") or "").strip().lower()
                    ):
                        _flag_shot(
                            b.global_index,
                            f"repeats recent visual_history setting {cur_setting!r}",
                        )
                        break
                    if cur_subj and cur_subj == str(prev.get("subject") or "").strip().lower():
                        _flag_shot(
                            b.global_index,
                            f"repeats recent visual_history subject {cur_subj!r}",
                        )
                        break
        ai_history.append(meta)

    if len(ai_beats_list) >= 4 and min_human_ratio > 0.0:
        needed_humans = math.ceil(min_human_ratio * len(ai_beats_list))
        deficit = needed_humans - human_hits
        if deficit > 0:
            for preferred_kind in ("medium_human_action", "wide_environment"):
                for b in ai_beats_list:
                    if deficit <= 0:
                        break
                    if b.shot_type == preferred_kind and not has_human_presence(
                        b.prompt, b.visual_meta
                    ):
                        _flag_shot(
                            b.global_index,
                            f"human presence required to meet min_human_presence_ratio ({min_human_ratio:.2f})",
                        )
                        deficit -= 1

    return timeline_issues, shot_issues


class StoryboardCritic:
    """Pre-render storyboard validator and selective auto-repair controller."""

    def __init__(self, settings: Any, llm_provider: LLMProvider | None = None) -> None:
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="storyboard_critic")

    def _llm_audit(self, beats: list[PlannedBeat], *, video_title: str = "") -> dict[int, str]:
        """Run one batched LIGHT-tier LLM audit over AI-image shots; returns ``{global_index: reason}``."""
        if self._llm is None or not getattr(self._settings, "enable_storyboard_critic", False):
            return {}
        ai_beats = [b for b in beats if b.visual_type == "ai_image"]
        if not ai_beats:
            return {}

        hist_win = int(getattr(self._settings, "visual_history_window_size", 3))
        banlist = list(getattr(self._settings, "cliche_banlist_list", []) or [])
        payload = []
        running_hist: list[dict[str, Any]] = []
        for b in ai_beats:
            payload.append(
                {
                    "global_index": b.global_index,
                    "spoken_text": b.spoken_text,
                    "shot_type": b.shot_type,
                    "prompt": b.prompt,
                    "visual_meta": b.visual_meta,
                    "prior_visual_history": running_hist[-hist_win:] if hist_win > 0 else [],
                }
            )
            if b.visual_meta:
                running_hist.append(b.visual_meta)

        try:
            system = render_prompt(
                load_prompt("storyboard_critic.system"),
                cliche_banlist=", ".join(banlist) if banlist else "(none)",
                title=video_title or "",
                shots_json=json.dumps(payload, ensure_ascii=False, indent=2),
            )
            model = select_model(
                self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
            )
            resp = self._llm.complete(
                "Audit the candidate AI image prompts and return ONLY JSON.",
                system=system,
                temperature=0.0,
                max_tokens=self._settings.llm_max_tokens,
                model=model,
            )
            try:
                parsed = json.loads(resp.text.strip())
            except json.JSONDecodeError:
                parsed = json.loads(extract_json(resp.text))
            results = parsed.get("results") if isinstance(parsed, dict) else None
            if not isinstance(results, list):
                return {}
            failures: dict[int, str] = {}
            valid_ids = {b.global_index for b in ai_beats}
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                g_id = entry.get("global_index")
                if isinstance(g_id, int) and g_id in valid_ids and entry.get("passed") is False:
                    failures[g_id] = str(entry.get("reason") or "failed relevance/variety audit")
            return failures
        except Exception as exc:
            self._log.warning("storyboard_critic_llm_skipped", error=str(exc))
            return {}

    def audit_and_repair(
        self,
        beats: list[PlannedBeat],
        *,
        regenerate_shot_fn: Callable[
            [PlannedBeat, str, list[dict[str, Any]]], DualPromptResult | None
        ]
        | None = None,
        video_title: str = "",
    ) -> StoryboardValidationReport:
        """Validate ``beats`` in place, selectively regenerating only failing AI prompts up to
        ``critic_max_retries`` and falling back deterministically so zero broken shots reach render."""
        repairs: list[str] = []
        if not beats:
            return StoryboardValidationReport(ok=True)

        prog_enabled = bool(getattr(self._settings, "enable_programmatic_graphics", True)) and bool(
            getattr(self._settings, "enabled_motion_templates", True)
        )
        open_win = float(getattr(self._settings, "opening_peak_window_sec", 30.0))
        ban_kb_open = bool(getattr(self._settings, "ban_ken_burns_in_opening", True))
        anchor_max = float(getattr(self._settings, "programmatic_anchor_max_sec", 60.0))
        rotate = bool(getattr(self._settings, "enforce_shot_type_rotation", True))
        max_retries = int(getattr(self._settings, "critic_max_retries", 2))

        # Step 1: Deterministic structural & template_props clamp pass.
        if prog_enabled:
            if beats[0].visual_type != "programmatic_graphic":
                tid = pick_enabled_template(
                    "big_stat_card", self._settings, allowed_subset=HOOK_TEMPLATES
                )
                if tid:
                    beats[0].visual_type = "programmatic_graphic"
                    beats[0].template_id = tid
                    beats[0].template_props = build_deterministic_template_props(
                        tid, beats[0], settings=self._settings, video_title=video_title
                    )
                    repairs.append("promoted beat 0 to programmatic_graphic for silent preview")

            for b in beats:
                if b.viewer_start < open_win and ban_kb_open and b.visual_type == "ai_image":
                    tid = pick_enabled_template(
                        b.preferred_template or "split_comparison", self._settings
                    )
                    if tid:
                        b.visual_type = "programmatic_graphic"
                        b.template_id = tid
                        b.template_props = build_deterministic_template_props(
                            tid, b, settings=self._settings, video_title=video_title
                        )
                        repairs.append(
                            f"promoted opening beat {b.global_index} off banned ai_image"
                        )

            # Repair any anchor gap > anchor_max
            last_end = 0.0
            for b in beats:
                if b.visual_type == "programmatic_graphic":
                    last_end = b.viewer_end
                    continue
                if b.viewer_end - last_end > anchor_max:
                    can_ev = beat_supports_evidence_card(b, self._settings)
                    disallowed = () if can_ev else ("evidence_card",)
                    tid = pick_enabled_template(
                        b.preferred_template
                        or ("evidence_card" if can_ev else "architecture_flow"),
                        self._settings,
                        disallowed=disallowed,
                    )
                    if tid:
                        b.visual_type = "programmatic_graphic"
                        b.template_id = tid
                        b.template_props = build_deterministic_template_props(
                            tid, b, settings=self._settings, video_title=video_title
                        )
                        last_end = b.viewer_end
                        repairs.append(f"inserted periodic anchor on beat {b.global_index}")

            # Re-validate / auto-clamp all template_props, strip unverified/low-tier sources,
            # prevent non-quote evidence_cards, and break back-to-back template repeats.
            prev_tid: str | None = None
            usage_so_far: dict[str, int] = {}
            for idx_b, b in enumerate(beats):
                if b.visual_type != "programmatic_graphic":
                    continue
                can_ev = beat_supports_evidence_card(b, self._settings)
                disallowed = () if can_ev else ("evidence_card",)
                tid = b.template_id or pick_enabled_template(
                    "big_stat_card", self._settings, disallowed=disallowed
                )
                if tid == "evidence_card" and not can_ev:
                    tid = pick_enabled_template(
                        "architecture_flow",
                        self._settings,
                        previous_template=prev_tid,
                        usage_counts=usage_so_far,
                        disallowed=("evidence_card",),
                    )
                    b.template_props = build_deterministic_template_props(
                        tid or "big_stat_card", b, settings=self._settings, video_title=video_title
                    )
                    repairs.append(
                        f"re-routed unquoted evidence_card on beat {b.global_index} to {tid}"
                    )
                elif (
                    tid
                    and prev_tid
                    and not b.is_comment_friction
                    and (
                        tid == prev_tid
                        or (
                            TEMPLATE_FAMILY_OF.get(tid) == "comparison_rubric"
                            and TEMPLATE_FAMILY_OF.get(prev_tid) == "comparison_rubric"
                        )
                    )
                ):
                    alt_tid = pick_enabled_template(
                        tid,
                        self._settings,
                        previous_template=prev_tid,
                        allowed_subset=HOOK_TEMPLATES if idx_b == 0 else None,
                        usage_counts=usage_so_far,
                        disallowed=disallowed,
                    )
                    if alt_tid and alt_tid != tid:
                        tid = alt_tid
                        b.template_props = build_deterministic_template_props(
                            tid, b, settings=self._settings, video_title=video_title
                        )
                        repairs.append(
                            f"rotated adjacent template on beat {b.global_index} to {tid}"
                        )
                if tid:
                    _, _, max_c = self._settings.template_spec(tid)
                    b.template_id = tid
                    raw_p = dict(b.template_props or {})
                    verified_src = clean_verified_source(b.visual_source, self._settings)
                    cur_src = str(raw_p.get("source") or "").strip()
                    if cur_src and is_low_tier_source(cur_src, self._settings):
                        raw_p["source"] = ""
                        repairs.append(f"stripped low-tier source on beat {b.global_index}")
                    elif cur_src and not verified_src and len(beats) >= 6:
                        raw_p["source"] = ""
                        repairs.append(f"stripped unverified source on beat {b.global_index}")
                    elif (
                        cur_src
                        and verified_src
                        and verified_src.lower() not in cur_src.lower()
                        and cur_src.lower() not in verified_src.lower()
                    ):
                        raw_p["source"] = verified_src
                        repairs.append(f"normalized source badge on beat {b.global_index}")
                    b.template_props = validate_template_props(tid, raw_p, max_chars=max_c)
                    usage_so_far[tid] = usage_so_far.get(tid, 0) + 1
                    prev_tid = tid

        # Normalize AI shot rotation before auditing prompts.
        if rotate:
            ai_c = 0
            for b in beats:
                if b.visual_type == "ai_image":
                    b.shot_type = SHOT_TYPE_CYCLE[ai_c % len(SHOT_TYPE_CYCLE)]
                    if b.visual_meta:
                        b.visual_meta["shot_type"] = b.shot_type
                    ai_c += 1

        # Step 2: Selective regeneration loop for failing AI shots ONLY.
        retries_used = 0
        by_global = {b.global_index: b for b in beats}

        for attempt in range(max_retries + 1):
            t_issues, s_issues = validate_storyboard_deterministic(beats, self._settings)
            llm_failures = (
                self._llm_audit(beats, video_title=video_title) if attempt == 0 or s_issues else {}
            )
            for g_id, reason in llm_failures.items():
                s_issues.setdefault(g_id, []).append(f"llm_critic: {reason}")

            failing_ai_ids = [
                g_id
                for g_id in sorted(s_issues)
                if g_id in by_global and by_global[g_id].visual_type == "ai_image"
            ]
            if not failing_ai_ids or attempt >= max_retries or regenerate_shot_fn is None:
                break

            retries_used += 1
            self._log.info(
                "storyboard_critic_retry",
                attempt=retries_used,
                failing_shots=failing_ai_ids,
            )
            for g_id in failing_ai_ids:
                target_b = by_global[g_id]
                prior_hist = [
                    other.visual_meta
                    for other in beats
                    if other.global_index < g_id
                    and other.visual_type == "ai_image"
                    and other.visual_meta
                ]
                reason_text = "; ".join(s_issues[g_id])
                regen = regenerate_shot_fn(target_b, reason_text, prior_hist)
                if regen is None:
                    continue
                if regen.redirect_to_graphic and prog_enabled:
                    tid = pick_enabled_template(
                        regen.suggested_template or "evidence_card", self._settings
                    )
                    if tid:
                        target_b.visual_type = "programmatic_graphic"
                        target_b.template_id = tid
                        target_b.template_props = build_deterministic_template_props(
                            tid, target_b, settings=self._settings, video_title=video_title
                        )
                        target_b.shot_type = ""
                        repairs.append(f"redirected conceptual beat {g_id} to {tid}")
                        continue
                target_b.prompt = regen.prompt
                target_b.visual_meta = regen.visual_meta
                target_b.visual_meta["shot_type"] = target_b.shot_type
                repairs.append(f"regenerated ai_image beat {g_id} (attempt {retries_used})")

            # Re-sync shot rotation if a redirect changed the AI sequence.
            if rotate:
                ai_c = 0
                for b in beats:
                    if b.visual_type == "ai_image":
                        b.shot_type = SHOT_TYPE_CYCLE[ai_c % len(SHOT_TYPE_CYCLE)]
                        if b.visual_meta:
                            b.visual_meta["shot_type"] = b.shot_type
                        ai_c += 1

        # Step 3: Deterministic clean fallback for any residual AI-prompt defects after retries.
        _, residual_shot_issues = validate_storyboard_deterministic(beats, self._settings)
        banlist = list(getattr(self._settings, "cliche_banlist_list", []) or [])
        style_bible = str(getattr(self._settings, "image_style_bible", "") or "")
        for g_id, reasons in sorted(residual_shot_issues.items()):
            cand = by_global.get(g_id)
            if cand is None or cand.visual_type != "ai_image":
                continue
            prior_hist = [
                other.visual_meta
                for other in beats
                if other.global_index != g_id
                and other.visual_type == "ai_image"
                and other.visual_meta
            ]
            # Scrub cliches or replace with clean editorial fallback so bad prompts never ship.
            if any(
                "repeats recent visual_history" in r
                or "exceeds video-wide setting archetype" in r
                or "human presence required" in r
                or "missing structured" in r
                or "empty" in r
                for r in reasons
            ):
                fb = build_editorial_fallback_prompt(
                    cand.spoken_text, cand.shot_type, self._settings, visual_history=prior_hist
                )
                cand.prompt = fb.prompt
                cand.visual_meta = fb.visual_meta
                repairs.append(f"fallback-repaired ai_image beat {g_id}")
            else:
                cand.prompt = scrub_physical_text_cues(
                    scrub_cliche_phrases(cand.prompt, banlist, style_bible=style_bible)
                )
                if not cand.visual_meta or not cand.visual_meta.get("subject"):
                    fb = build_editorial_fallback_prompt(
                        cand.spoken_text, cand.shot_type, self._settings, visual_history=prior_hist
                    )
                    cand.visual_meta = fb.visual_meta
                repairs.append(f"scrubbed cliches/text on ai_image beat {g_id}")

        final_t_issues, final_s_issues = validate_storyboard_deterministic(beats, self._settings)
        ok = not final_t_issues and not final_s_issues
        if not ok:
            self._log.warning(
                "storyboard_critic_residual_issues",
                timeline_issues=final_t_issues,
                shot_issues=final_s_issues,
            )
        return StoryboardValidationReport(
            ok=ok,
            timeline_issues=final_t_issues,
            shot_issues=final_s_issues,
            repairs_applied=repairs,
            retries_used=retries_used,
        )
