"""Deterministic rubric checks + heuristics for the Judge (Ch. 9.3a, 9.4)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..models import Script
from ..safeguards.disclosure import description_has_disclosure
from ..safeguards.grounding import extract_stats

GENERIC_PHRASES = (
    "network more",
    "update your resume",
    "work hard",
    "stay positive",
    "be yourself",
    "follow your passion",
    "just apply",
    "believe in yourself",
    "hustle",
    "think outside the box",
)
_CAP_TERM_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
_STEP_CUE_RE = re.compile(r"\b(step|first|second|third|next|then|start by|do this|try|use)\b", re.I)
_NONOBVIOUS_RE = re.compile(
    r"\b(counterintuitive|surprising|actually|contrary|reframe|hidden|overlooked|myth)\b", re.I
)
_ENGAGE_RE = re.compile(
    r"\b(you|your|imagine|picture|here'?s|but|because|so|why|secret|mistake|nobody|"
    r"everyone|everybody|truth|actually|really|what if)\b",
    re.I,
)
_WIT_RE = re.compile(
    r"\b(like|imagine|basically|literally|honestly|apparently|meanwhile|turns out|"
    r"plot twist|spoiler|newsflash|surprise|as if|picture this)\b",
    re.I,
)


def all_text(script: Script) -> str:
    return script.hook + " " + " ".join(s.narration for s in script.scenes)


def generic_hits(script: Script) -> int:
    text = all_text(script).lower()
    return sum(text.count(phrase) for phrase in GENERIC_PHRASES)


def specificity_score(script: Script) -> float:
    text = all_text(script)
    stats = len(extract_stats(text))
    proper = len(_CAP_TERM_RE.findall(text))
    raw = 2.0 + 1.3 * stats + 0.12 * proper - 1.0 * generic_hits(script)
    return _clamp(raw)


def specificity_why(script: Script) -> str:
    text = all_text(script)
    stats = len(extract_stats(text))
    proper = len(_CAP_TERM_RE.findall(text))
    hits = generic_hits(script)
    parts: list[str] = []
    if stats == 0:
        parts.append("no specific numbers or statistics found")
    else:
        parts.append(f"{stats} statistic(s) found")
    if hits > 0:
        parts.append(f"{hits} generic-phrase hit(s) — avoid phrases like 'network more' or 'work hard'")
    if proper < 3:
        parts.append("few named roles, tools, or companies cited")
    return "; ".join(parts) or "specificity looks adequate."


def hook_score(script: Script) -> float:
    hook = script.hook or (script.scenes[0].narration if script.scenes else "")
    words = len(hook.split())
    score = 4.0
    if extract_stats(hook):
        score += 4.0
    if words <= 25:
        score += 2.0
    elif words <= 40:
        score += 1.0
    return _clamp(score)


def hook_why(script: Script) -> str:
    hook = script.hook or (script.scenes[0].narration if script.scenes else "")
    words = len(hook.split())
    parts: list[str] = []
    if not extract_stats(hook):
        parts.append("no specific number or statistic in the hook — open with a concrete figure")
    if words > 40:
        parts.append(f"hook is {words} words, which is too long (aim for ≤25)")
    return "; ".join(parts) or "hook looks good."


def _shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if len(tokens) < n:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def hook_similarity(hook: str, recent_hooks: Sequence[str]) -> float:
    target = _shingles(hook)
    return max((_jaccard(target, _shingles(h)) for h in recent_hooks), default=0.0)


def duplicate_scene_pairs(script: Script, *, threshold: float = 0.5) -> list[tuple[int, int, float]]:
    """Pairs of scenes whose narration is near-duplicate (3-gram Jaccard >= threshold). Recycling the
    same sentences/facts across scenes reads as lazy padding and drives viewer churn, so it is caught
    deterministically. Returns (scene_a, scene_b, similarity), 1-based for human-readable feedback."""
    shings = [_shingles(sc.narration) for sc in script.scenes]
    out: list[tuple[int, int, float]] = []
    for a in range(len(shings)):
        for b in range(a + 1, len(shings)):
            sim = _jaccard(shings[a], shings[b])
            if sim >= threshold:
                out.append((a + 1, b + 1, round(sim, 2)))
    return out


def redundancy_report(script: Script, *, threshold: float = 0.5) -> tuple[bool, str]:
    """(is_ok, detail): flag scripts that repeat whole scenes near-verbatim, with a specific note
    naming the offending scene pairs so the rewrite fixes exactly them."""
    dupes = duplicate_scene_pairs(script, threshold=threshold)
    if not dupes:
        return True, "every scene is distinct."
    listed = ", ".join(f"scenes {a} & {b} (~{int(sim * 100)}% identical)" for a, b, sim in dupes[:8])
    return False, (
        "REPEATED SCENES (lazy padding that drives viewers away): "
        f"{listed}. Rewrite each flagged scene to make a genuinely DIFFERENT point in fresh words; "
        "state each fact or statistic at most ONCE in the whole script, and never reuse a sentence."
    )


def dedupe_scene_indices(script: Script, *, threshold: float = 0.5) -> list[int]:
    """Indices of the scenes to KEEP: walking in order, drop any scene whose narration is a
    near-duplicate (3-gram Jaccard >= threshold) of an already-kept scene. Keeps the FIRST of each
    duplicate cluster, so a generator that padded by recycling scenes is trimmed to its distinct
    ones. Used by the Script Generator to GUARANTEE (in code) the draft the Judge sees has no
    near-verbatim repeats — the same measure `duplicate_scene_pairs` flags."""
    kept: list[int] = []
    kept_shingles: list[set] = []
    for i, scene in enumerate(script.scenes):
        sh = _shingles(scene.narration)
        if any(_jaccard(sh, ks) >= threshold for ks in kept_shingles):
            continue
        kept.append(i)
        kept_shingles.append(sh)
    return kept


@dataclass
class FreshnessResult:
    score: float
    fatigue: bool
    similarity: float


def freshness_and_fatigue(
    template_id: str,
    hook: str,
    recent_template_ids: Sequence[str],
    recent_hooks: Sequence[str],
    *,
    jaccard_threshold: float = 0.6,
) -> FreshnessResult:
    fatigue = bool(recent_template_ids) and recent_template_ids[0] == template_id
    similarity = hook_similarity(hook, recent_hooks)
    if similarity >= jaccard_threshold:
        fatigue = True

    score = 10.0 - 3.0 * recent_template_ids.count(template_id) - 6.0 * similarity
    return FreshnessResult(score=_clamp(score), fatigue=fatigue, similarity=round(similarity, 3))


def freshness_why(template_id: str, fresh: FreshnessResult, recent_template_ids: Sequence[str]) -> str:
    parts: list[str] = []
    count = list(recent_template_ids).count(template_id)
    if count > 0:
        parts.append(f"template '{template_id}' used {count} time(s) recently — vary the structure")
    if fresh.similarity > 0.3:
        parts.append(f"hook too similar to a recent one (Jaccard {fresh.similarity:.2f}) — rewrite the opening")
    return "; ".join(parts) or "freshness looks adequate."


def compliance_check(script: Script) -> tuple[float, bool]:
    ok = bool(script.synthetic_disclosure) and description_has_disclosure(script.description)
    return (10.0 if ok else 0.0), ok


def heuristic_actionability(script: Script) -> float:
    text = all_text(script)
    steps = len(_STEP_CUE_RE.findall(text))
    stats = len(extract_stats(text))
    raw = 3.0 + 1.0 * min(steps, 4) + 0.6 * min(stats, 5) - 1.5 * generic_hits(script)
    return _clamp(raw)


def heuristic_insight(script: Script) -> float:
    text = all_text(script)
    stats = len(extract_stats(text))
    nonobvious = len(_NONOBVIOUS_RE.findall(text))
    raw = 2.5 + 0.8 * min(stats, 5) + 1.2 * min(nonobvious, 3) - 2.0 * generic_hits(script)
    return _clamp(raw)


def heuristic_engagement(script: Script) -> float:
    """Crude retention proxy (deterministic fallback): curiosity hooks, direct address, questions."""
    text = all_text(script)
    hooks = len(_ENGAGE_RE.findall(text))
    stats = len(extract_stats(text))
    questions = text.count("?")
    raw = 4.0 + 0.6 * min(hooks, 5) + 0.35 * min(stats, 5) + 0.6 * min(questions, 3) - 1.5 * generic_hits(script)
    return _clamp(raw)


def heuristic_wittiness(script: Script) -> float:
    """Crude entertainment proxy (deterministic fallback): analogies, asides, playful markers."""
    text = all_text(script)
    wit = len(_WIT_RE.findall(text))
    excls = text.count("!")
    questions = text.count("?")
    raw = 4.5 + 0.9 * min(wit, 4) + 0.6 * min(excls, 3) + 0.3 * min(questions, 3) - 1.5 * generic_hits(script)
    return _clamp(raw)


_ENDING_CTA_RE = re.compile(
    r"\b(subscribe|follow (?:me|along|for)|hit (?:the |that )?(?:like|bell|subscribe|follow)|"
    r"ring the bell|like this video|drop a like|smash (?:the )?like|leave a comment|share this)\b",
    re.I,
)
_ENDING_SIGNOFF_RE = re.compile(
    r"\b(see you|catch you|until next|next (?:one|time|video)|thanks for watching|"
    r"that'?s a wrap|stay tuned|take care|see ya|before you go)\b",
    re.I,
)


def ending_report(script: Script) -> tuple[float, str]:
    """Word-match on the closing narration (last 1-2 scenes). A real ending needs BOTH a
    like/subscribe nudge AND a warm sign-off, so each is worth 5 (both = 10, one = 5, neither = 0).
    Returns (score, a specific note on what is missing) so the Judge tells the Generator EXACTLY
    what to add on a revision instead of looping blindly."""
    scenes = script.scenes
    if not scenes:
        return 0.0, "the script has no scenes to close."
    tail = " ".join(s.narration for s in scenes[-2:])
    cta = bool(_ENDING_CTA_RE.search(tail))
    signoff = bool(_ENDING_SIGNOFF_RE.search(tail))
    if cta and signoff:
        return 10.0, "closes with both a like/subscribe nudge and a sign-off."
    if cta:
        return 5.0, "has a like/subscribe nudge but NO sign-off — add a warm 'see you in the next one' close."
    if signoff:
        return 5.0, "has a sign-off but NO like/subscribe nudge — add an explicit 'subscribe' ask."
    return 0.0, "ends abruptly: NO like/subscribe nudge and NO sign-off — the final scene must add BOTH."


def heuristic_ending(script: Script) -> float:
    return ending_report(script)[0]


def _clamp(value: float, low: float = 0.0, high: float = 10.0) -> float:
    return round(max(low, min(high, value)), 2)
