"""Agent 2 tests: grounding repair, reformat-retry, disclosure injection (Ch. 8)."""

from __future__ import annotations

from content_foundry.agents import ScriptGenerator
from content_foundry.safeguards.grounding import extract_stats
from content_foundry.templates import get_template


def test_generate_good_script(settings, data_brief, fakes):
    llm = fakes.LLM()
    script = ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    assert llm.call_count == 1
    assert script.word_count > 0
    assert script.grounded_fact_refs == [0, 1, 2]
    assert script.synthetic_disclosure is True
    assert "synthetic" in script.description.lower()


def test_reformat_retry_on_bad_json(settings, data_brief, fakes):
    llm = fakes.LLM(bad_then_good=True)
    script = ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    assert llm.call_count == 2  # one bad + one reformat retry
    assert script.hook


def test_ungrounded_stat_is_stripped(settings, data_brief, fakes):
    payload = {
        "title_options": ["t"],
        "hook": "A grounded hook with 31% drop.",
        "scenes": [
            {"index": 0, "narration": "Postings fell 31% this year.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": 0},
            {"index": 1, "narration": "Salaries surged 999% overnight per nobody.",
             "on_screen_text": None, "b_roll_keywords": [], "fact_ref": None},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [],
        "thumbnail_concept": "x", "grounded_fact_refs": [0],
    }
    llm = fakes.LLM(script_json=payload)
    script = ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    # The ungrounded "999%" must have been stripped during the repair pass.
    assert "999%" not in script.scenes[1].narration
    assert extract_stats(script.scenes[1].narration) == []


def test_disclosure_injected_when_missing(settings, data_brief, fakes):
    payload = {
        "title_options": ["t"], "hook": "Specific 31% hook.",
        "scenes": [{"index": 0, "narration": "Postings fell 31%.", "on_screen_text": None,
                    "b_roll_keywords": [], "fact_ref": 0}],
        "cta": "x", "description": "No disclosure here.", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0],
    }
    llm = fakes.LLM(script_json=payload)
    script = ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    assert "synthetic" in script.description.lower()


def test_every_stat_scene_cites_its_source(settings, data_brief, fakes):
    # HARD RULE: a scene stating a statistic must never appear without its source on screen —
    # enforced deterministically, so it holds even when the model omits it (on_screen_text=None).
    payload = {
        "title_options": ["t"], "hook": "A grounded 31% hook.",
        "scenes": [
            {"index": 0, "narration": "Postings fell 31% this year.", "on_screen_text": None,
             "b_roll_keywords": [], "fact_ref": 0},
            {"index": 1, "narration": "Adjacent roles are the smarter move; build a portfolio.",
             "on_screen_text": "No numbers here", "b_roll_keywords": [], "fact_ref": None},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0],
    }
    script = ScriptGenerator(settings, fakes.LLM(script_json=payload)).run(
        "R", data_brief, get_template("contrarian")
    )
    stat_scene = next(s for s in script.scenes if extract_stats(s.narration))
    assert "source" in (stat_scene.on_screen_text or "").lower()  # source was stamped
    # a scene with no statistic is left untouched — no spurious citation
    plain = next(s for s in script.scenes if not extract_stats(s.narration))
    assert plain.on_screen_text == "No numbers here"


def test_fact_ref_list_is_coerced(settings, data_brief, fakes):
    # Local models sometimes cite several facts as a list ([0, 2]); SceneCue needs a single int.
    payload = {
        "title_options": ["t"], "hook": "A grounded 31% hook.",
        "scenes": [{"index": 0, "narration": "Postings fell 31% this year.",
                    "on_screen_text": None, "b_roll_keywords": [], "fact_ref": [0, 2]}],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0],
    }
    script = ScriptGenerator(settings, fakes.LLM(script_json=payload)).run(
        "R", data_brief, get_template("contrarian")
    )
    assert script.scenes[0].fact_ref == 0  # first usable index kept
    assert "31%" in script.scenes[0].narration  # stayed grounded, not stripped
