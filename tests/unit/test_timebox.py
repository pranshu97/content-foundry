"""Unit: time-boxing helpers — year-stamp titles, keep ideas evergreen (future plan 3)."""

from __future__ import annotations

from career_engine.production.timebox import build_time_context, has_year, timebox_title


def test_has_year_detects_4_digit_years():
    assert has_year("Best Career Advice in 2026")
    assert has_year("1999 throwback")
    assert not has_year("Best Career Advice")
    assert not has_year("Top 5 tips")  # a bare number is not a year


def test_timebox_title_appends_year_when_absent():
    assert timebox_title("Best Career Advice", 2026) == "Best Career Advice (2026)"


def test_timebox_title_is_idempotent_and_skips_existing_year():
    assert timebox_title("Best Career Advice in 2026", 2026) == "Best Career Advice in 2026"
    once = timebox_title("Do This Now", 2026)
    assert timebox_title(once, 2026) == once  # already stamped -> unchanged


def test_timebox_title_handles_empty():
    assert timebox_title("", 2026) == ""
    assert timebox_title("   ", 2026) == ""


def test_build_time_context_mentions_year():
    ctx = build_time_context(2026)
    assert "2026" in ctx and "evergreen" in ctx.lower()
