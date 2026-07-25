"""Unit: time context — keep videos evergreen, never mechanically year-stamp titles (future plan 3)."""

from __future__ import annotations

from content_foundry.production.timebox import build_time_context


def test_build_time_context_mentions_year_and_keeps_evergreen():
    ctx = build_time_context(2026)
    assert "2026" in ctx and "evergreen" in ctx.lower()
    # It must steer AWAY from stamping a bare year onto an otherwise timeless title.
    assert "time_sensitive" in ctx
    assert "(2026)" in ctx  # the guidance explicitly names the anti-pattern it forbids

