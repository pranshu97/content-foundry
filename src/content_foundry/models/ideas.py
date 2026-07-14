"""IdeaSelection artifact — records the ideas the Brainstormer generated and the exact one the run
went ahead with, so a run's creative choice is inspectable after the fact (Ch. 14)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .provenance import utcnow


def _views_human(views: int) -> str:
    """Compact view count for a picker line: 5_200_000 -> '5.2M', 43_000 -> '43K', 900 -> '900'."""
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M".replace(".0M", "M")
    if views >= 1_000:
        return f"{views / 1_000:.0f}K"
    return str(max(views, 0))


class MinedIdea(BaseModel):
    """A PROVEN idea: a real YouTube video that beat its own channel's median views by a wide margin,
    so the concept itself demonstrably resonates (independent of how big the channel is). Surfaced in
    the idea picker with a proof tag; only the clean ``title`` is what a chosen run actually builds."""

    title: str
    channel_title: str = ""
    views: int = 0
    multiple: float = 0.0  # views / the channel's median views — how strong an outlier it is
    video_url: str = ""

    def display(self) -> str:
        """Picker line, e.g. ``'Are devices listening to you  [Veritasium — 5M views, 8x avg]'``."""
        tag = self.channel_title.strip() or "YouTube"
        extra = f", {self.multiple:g}x avg" if self.multiple >= 1.5 else ""
        return f"{self.title}  [{tag} — {_views_human(self.views)} views{extra}]"


class IdeaSelection(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["ideas"] = "ideas"
    seed: str = ""  # the operator's --idea / focus that steered the brainstorm (may be blank)
    brainstorm_enabled: bool = True
    # brainstorm = picked from a generated idea; seed = brainstorm off / no ideas, raw --idea used;
    # custom = the interactive chooser returned an idea the operator typed in themselves.
    source: Literal["brainstorm", "seed", "custom"] = "brainstorm"
    generated: list[str] = Field(default_factory=list)  # every idea proposed this run
    chosen: str = ""  # the exact idea the run committed to
    chosen_index: int = -1  # index into ``generated``, or -1 when the pick is not from that list
    generated_at: datetime = Field(default_factory=utcnow)
