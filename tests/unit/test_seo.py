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
    youtube_safe_text,
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


def test_optimize_title_never_year_stamps():
    # The published title is never mechanically year-stamped (the recurring "(2026) on every title"
    # complaint) — a genuinely dated topic gets its year woven into a title option by the writer.
    assert optimize_title(["Best Career Advice"], max_chars=70) == "Best Career Advice"
    # A year the writer put in a title option itself is preserved untouched.
    assert optimize_title(["2026 Salary Report"], max_chars=70) == "2026 Salary Report"


def test_optimize_title_truncates_overlong():
    out = optimize_title(["x" * 100], max_chars=20)
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


def test_youtube_safe_text_replaces_forbidden_angle_brackets():
    # YouTube's API 400s (invalidDescription) on a title/description containing '<' or '>' — a chapter
    # built from an on_screen_text like "PROCESS > RESULT" is the usual culprit.
    assert youtube_safe_text("PROCESS > RESULT") == "PROCESS › RESULT"
    assert youtube_safe_text("a < b, c > d") == "a ‹ b, c › d"
    cleaned = youtube_safe_text("Chapters:\n0:00 Intro\n1:12 PROCESS > RESULT")
    assert "<" not in cleaned and ">" not in cleaned
    assert youtube_safe_text("") == ""  # empty in, empty out


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
    good_script.title_options = ["Best Career Advice"]  # never mechanically year-stamped
    good_script.time_sensitive = True  # even so, the title stays yearless (writer weaves years, not us)
    meta = optimize_metadata(good_script, _visuals(12.0), settings)
    assert meta.title == "Best Career Advice"
    assert "tech careers" in meta.tags
    assert "Chapters:" in meta.description  # 3 scenes x 12s qualifies


def test_channel_cta_block_reflects_config(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache
    from content_foundry.production.seo import channel_cta_block

    monkeypatch.setenv("CHANNEL_CTA_ENABLED", "true")
    monkeypatch.setenv("CHANNEL_CTA_TEXT", "Subscribe for more.")
    monkeypatch.setenv("YOUTUBE_CHANNEL_URL", "https://youtube.com/@x")
    reset_settings_cache()
    block = channel_cta_block(get_settings())
    assert "Subscribe for more." in block and "https://youtube.com/@x" in block
    monkeypatch.setenv("CHANNEL_CTA_ENABLED", "false")
    reset_settings_cache()
    assert channel_cta_block(get_settings()) == ""


def test_optimize_description_adds_channel_cta_and_leads_with_shorts_hashtag():
    desc = optimize_description(
        "Body.", cta="", tags=["ml career"], chapters=[], add_chapters=False,
        channel_cta="Subscribe for more.\n▶ https://youtube.com/@x", shorts_hashtag="#Shorts",
    )
    assert "Subscribe for more." in desc and "https://youtube.com/@x" in desc
    assert desc.split("\n\n")[-1].startswith("#Shorts ")  # #Shorts leads the hashtag line


def test_optimize_metadata_short_skips_chapters_and_tags_shorts(monkeypatch, good_script):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("CONTENT_FORMAT", "short")
    reset_settings_cache()
    good_script.title_options = ["A Punchy Short Idea"]
    meta = optimize_metadata(good_script, _visuals(12.0), get_settings())
    assert "Chapters:" not in meta.description  # chapters don't apply to a Short
    assert "#Shorts" in meta.description
