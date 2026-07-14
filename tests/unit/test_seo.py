"""Unit: deterministic discoverability metadata — titles, tags, chapters, description (plans 4-5)."""

from __future__ import annotations

from content_foundry.models import Provenance, SceneVisual, VisualPackage
from content_foundry.production.seo import (
    build_chapters,
    hashtags,
    optimize_description,
    optimize_metadata,
    optimize_tags,
    optimize_title,
    pick_title,
)


# ------------------------------------------------------------------ tags
def test_optimize_tags_normalizes_dedups_and_seeds_niche():
    tags = optimize_tags(
        ["Tech Careers", "tech careers", "  Junior Dev  ", "x" * 40],
        niche="tech careers",
        channel_keywords=["Career Advice"],
        max_tags=5,
    )
    assert tags == ["tech careers", "career advice", "junior dev"]  # niche first, dup + long dropped


def test_optimize_tags_caps_count():
    tags = optimize_tags(["a", "b", "c", "d"], niche="n", channel_keywords=None, max_tags=2)
    assert tags == ["n", "a"]


# ----------------------------------------------------------------- title
def test_pick_title_prefers_within_length_and_numeric():
    title = pick_title(["A title that is definitely longer than twenty chars", "Top 5 Moves"], max_chars=20)
    assert title == "Top 5 Moves"


def test_pick_title_falls_back_when_empty():
    assert pick_title([], max_chars=70) == "Career Advice"


def test_optimize_title_year_stamps_when_time_sensitive():
    assert optimize_title(
        ["Best Career Advice"], year=2026, time_box=True, time_sensitive=True, max_chars=70
    ) == "Best Career Advice (2026)"


def test_optimize_title_skips_year_when_not_time_sensitive():
    # No year stamp when the writer didn't flag the topic time-sensitive, even with time_box on.
    # (A how/why/what title that IS time-bound gets flagged true by the LLM instead of guessed here.)
    assert optimize_title(
        ["How Recommendation Engines Work"], year=2026, time_box=True, time_sensitive=False,
        max_chars=70,
    ) == "How Recommendation Engines Work"


def test_optimize_title_truncates_overlong():
    out = optimize_title(["x" * 100], year=2026, time_box=False, time_sensitive=False, max_chars=20)
    assert len(out) <= 20 and out.endswith("…")


# --------------------------------------------------------------- chapters
def test_build_chapters_happy_path():
    chapters = build_chapters([(12.0, "Intro"), (15.0, "Body"), (20.0, "End")])
    assert chapters == [("0:00", "Intro"), ("0:12", "Body"), ("0:27", "End")]


def test_build_chapters_rejects_too_few_or_too_short():
    assert build_chapters([(12.0, "a"), (12.0, "b")]) == []  # < 3 chapters
    assert build_chapters([(12.0, "a"), (5.0, "b"), (12.0, "c")]) == []  # one < 10s
    assert build_chapters([(12.0, ""), (12.0, "  "), (12.0, "c")]) == []  # blank labels dropped


# ------------------------------------------------------------ description
def test_hashtags_camelcase_top_three():
    assert hashtags(["tech careers", "junior developer", "2026 job market", "extra"]) == [
        "#TechCareers",
        "#JuniorDeveloper",
        "#2026JobMarket",
    ]


def test_optimize_description_composes_blocks():
    desc = optimize_description(
        "Base body.",
        cta="Subscribe now.",
        tags=["tech careers"],
        chapters=[("0:00", "Intro"), ("0:12", "Body"), ("0:27", "End")],
        add_chapters=True,
    )
    assert "Base body." in desc
    assert "Subscribe now." in desc
    assert "Chapters:\n0:00 Intro" in desc
    assert "#TechCareers" in desc
    assert "synthetic" not in desc.lower()  # disclosure is the Publisher's job, not SEO's


def test_optimize_description_does_not_duplicate_cta():
    desc = optimize_description("Please Subscribe now.", cta="Subscribe now.", tags=[], chapters=[])
    assert desc.lower().count("subscribe now") == 1


# -------------------------------------------------------------- compose
def _visuals(duration: float) -> VisualPackage:
    return VisualPackage(
        run_id="R", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean",
        scenes=[
            SceneVisual(scene_index=i, kind="image", path=f"assets/scenes/scene_{i}.png",
                        source="card", prompt_or_query="p", duration_sec=duration)
            for i in range(3)
        ],
        provenance=Provenance(produced_by="visuals"),
    )


def test_optimize_metadata_end_to_end(settings, good_script):
    good_script.title_options = ["Best Career Advice"]  # yearless => optimizer stamps the year
    good_script.time_sensitive = True  # flagged time-sensitive => year stamped
    meta = optimize_metadata(good_script, _visuals(12.0), settings)
    year = settings.effective_content_year
    assert meta.title == f"Best Career Advice ({year})"
    assert "tech careers" in meta.tags
    assert "Chapters:" in meta.description  # 3 scenes x 12s qualifies
