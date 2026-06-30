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
