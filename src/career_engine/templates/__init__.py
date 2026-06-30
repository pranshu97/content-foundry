"""Template registry + anti-fatigue selection logic (Ch. 16.3).

Selection is a pure function of the *recent template ids* the orchestrator supplies (it owns the
DB query). This keeps the ``templates`` package free of persistence imports.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from .definitions import ALL_TEMPLATES, Template

TEMPLATES: dict[str, Template] = {t.id: t for t in ALL_TEMPLATES}

# Perspective modifiers layered on a Judge-forced shift so even a reused structure feels fresh.
PERSPECTIVE_MODIFIERS: tuple[str, ...] = (
    "Switch to a contrarian, skeptical lens.",
    "Rewrite in second-person, present-tense, talking directly to the viewer.",
    "Use a future-tense, scenario-driven framing ('by this time next year...').",
    "Open with a first-person practitioner anecdote, then generalise.",
    "Adopt a myth-busting, evidence-first stance.",
)


def get_template(template_id: str) -> Template:
    try:
        return TEMPLATES[template_id]
    except KeyError as exc:
        raise KeyError(
            f"Unknown template_id '{template_id}'. Known: {sorted(TEMPLATES)}"
        ) from exc


def _staleness(template_id: str, recent: Sequence[str]) -> int:
    """Higher = staler (better candidate). Templates absent from the window are stalest."""
    try:
        return recent.index(template_id)  # 0 == used most recently -> low staleness
    except ValueError:
        return len(recent) + 1  # never used in the window -> maximally stale


def select_template(
    recent_template_ids: Sequence[str],
    *,
    exclude: str | None = None,
    rng: random.Random | None = None,
) -> Template:
    """Pick the least-recently-used eligible template (weighted-random among the stale half).

    Args:
        recent_template_ids: template ids of the last N runs, most-recent first.
        exclude: a template to forbid (used on a Judge-forced structural shift).
        rng: inject a seeded ``random.Random`` for deterministic tests.
    """
    rng = rng or random.Random()
    candidates = [t for t in ALL_TEMPLATES if t.id != exclude]
    if not candidates:  # exclude removed everything (only happens with 1 template)
        candidates = list(ALL_TEMPLATES)

    ranked = sorted(candidates, key=lambda t: _staleness(t.id, recent_template_ids), reverse=True)
    half = max(1, len(ranked) // 2)
    pool = ranked[:half]
    weights = [_staleness(t.id, recent_template_ids) + 1 for t in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def pick_perspective_modifier(rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    return rng.choice(PERSPECTIVE_MODIFIERS)


__all__ = [
    "Template",
    "TEMPLATES",
    "ALL_TEMPLATES",
    "PERSPECTIVE_MODIFIERS",
    "get_template",
    "select_template",
    "pick_perspective_modifier",
]
