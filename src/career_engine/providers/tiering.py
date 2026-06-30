"""Model tiering — pick a heavy or light model per task (future plan 2).

Cost discipline: spend the expensive, capable model only on the hard, low-volume, creative call
(script generation) and route mechanical or high-volume calls (JSON repair, the Judge's discrete
1-5 scoring) to a cheaper, lighter model. When tiering is disabled the caller's legacy model is
used, so behaviour is unchanged by default.
"""

from __future__ import annotations

from enum import Enum


class TaskTier(str, Enum):
    HEAVY = "heavy"  # hard, creative, low-volume (initial script + revisions)
    LIGHT = "light"  # mechanical or high-volume (JSON repair, judge scoring)


def select_model(settings, tier: TaskTier, *, fallback: str) -> str:
    """Resolve the model id for ``tier``.

    ``fallback`` is the caller's legacy model, returned verbatim when tiering is off.
    """
    if not settings.llm_tiering_enabled:
        return fallback
    return settings.heavy_model if tier is TaskTier.HEAVY else settings.light_model
