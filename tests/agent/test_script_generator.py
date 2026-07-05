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


def test_leaked_fact_ref_is_never_voiced_or_captioned(settings, data_brief, fakes):
    # A model that writes the structured field inline (the reported bug) must never have it reach
    # the narration that feeds TTS + the subtitles. The real spoken words survive intact.
    payload = {
        "title_options": ["t"], "hook": "A grounded 31% hook.",
        "scenes": [
            {"index": 0, "narration": "Postings fell 31% this year (fact_ref: 0), a real shift.",
             "on_screen_text": None, "b_roll_keywords": ["office"], "fact_ref": 0},
            {"index": 1, "narration": "Recruiters skim resumes fast. fact_ref: 1 Lead with impact.",
             "on_screen_text": None, "b_roll_keywords": ["resume laptop"], "fact_ref": 1},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0, 1],
    }
    script = ScriptGenerator(settings, fakes.LLM(script_json=payload)).run(
        "R", data_brief, get_template("contrarian")
    )
    joined = " ".join(s.narration for s in script.scenes).lower()
    assert "fact_ref" not in joined
    assert "postings fell 31%" in script.scenes[0].narration.lower()
    assert "recruiters skim resumes fast" in script.scenes[1].narration.lower()


def test_clean_narration_removes_meta_but_keeps_prose():
    from content_foundry.agents.script_generator import _clean_narration

    assert _clean_narration("Great resume (fact_ref: 0) tips.") == "Great resume tips."
    assert _clean_narration("Lead with impact. fact_ref: 2") == "Lead with impact."
    assert _clean_narration("[b_roll: laptop] Recruiters skim.") == "Recruiters skim."
    # Legitimate parentheticals and the plain word "index" in prose are untouched.
    assert (
        _clean_narration("Track the S&P 500 (a stock index) daily.")
        == "Track the S&P 500 (a stock index) daily."
    )
    assert _clean_narration("No meta here at all.") == "No meta here at all."


def test_clean_narration_drops_bracketed_source_attribution():
    # A spoken source is duplication — the on-screen citation is stamped automatically. A bracketed
    # leak bounds cleanly, so it's safe to drop here; a bare word "source" in prose is left alone.
    from content_foundry.agents.script_generator import _clean_narration

    assert _clean_narration("Postings fell 31% (Source: Adzuna).") == "Postings fell 31%."
    assert (
        _clean_narration("Hiring slowed (according to U.S. BLS) sharply.")
        == "Hiring slowed sharply."
    )
    assert _clean_narration("Build one source of truth.") == "Build one source of truth."


def test_company_voice_is_neutralized_to_third_person():
    # Legal: the narration must never speak AS a company. "At Expedia Group we..." -> third person.
    from content_foundry.agents.script_generator import _neutralize_company_voice as n

    assert n("At Expedia group we boosted retention.") == "At Expedia group they boosted retention."
    assert n("At Google, our mission is clear.") == "At Google, their mission is clear."
    assert n("We at Amazon believe in customers.") == "They at Amazon believe in customers."
    # Viewer-inclusive and ordinary advice must be left completely alone.
    assert n("We've all been there when job hunting.") == "We've all been there when job hunting."
    assert n("At the end of the day, we win.") == "At the end of the day, we win."
    assert n("Tailor our resume to each role.") == "Tailor our resume to each role."


def test_company_voice_neutralized_through_full_run(settings, data_brief, fakes):
    payload = {
        "title_options": ["t"], "hook": "At Expedia group we changed hiring forever.",
        "scenes": [
            {"index": 0, "narration": "At Expedia group we rebuilt the resume screen entirely.",
             "on_screen_text": None, "b_roll_keywords": ["office"], "fact_ref": None, "sfx": None},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [],
    }
    script = ScriptGenerator(settings, fakes.LLM(script_json=payload)).run(
        "R", data_brief, get_template("contrarian")
    )
    assert "we " not in script.scenes[0].narration.lower()
    assert "they" in script.scenes[0].narration.lower()
    assert "we " not in script.hook.lower()  # the hook is cleaned too (it shows on the thumbnail)



def _all_null_sfx_payload():
    return {
        "title_options": ["t"], "hook": "A grounded 31% hook.",
        "scenes": [
            {"index": 0, "narration": "Here is the surprising truth about your resume today.",
             "on_screen_text": None, "b_roll_keywords": ["office"], "fact_ref": None, "sfx": None},
            {"index": 1, "narration": "Recruiters skim each resume for only a handful of seconds.",
             "on_screen_text": None, "b_roll_keywords": ["resume"], "fact_ref": None, "sfx": None},
            {"index": 2, "narration": "The average salary jumped to six figures for many roles.",
             "on_screen_text": None, "b_roll_keywords": ["money"], "fact_ref": None, "sfx": None},
            {"index": 3, "narration": "The biggest mistake is a vague, generic objective line.",
             "on_screen_text": None, "b_roll_keywords": ["error"], "fact_ref": None, "sfx": None},
            {"index": 4, "narration": "So tailor every bullet point to the job description you want.",
             "on_screen_text": None, "b_roll_keywords": ["laptop"], "fact_ref": None, "sfx": None},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [],
    }


def test_sound_design_fills_sfx_when_model_leaves_them_null(monkeypatch, data_brief, fakes):
    # The reported bug: sfx was null on every scene, so nothing ever mixed. With SFX enabled the
    # generator now guarantees a resolvable sprinkle by scene role.
    from content_foundry.config import get_settings, reset_settings_cache
    from content_foundry.providers.sfx import SfxLibrary

    monkeypatch.setenv("SFX_ENABLED", "true")
    reset_settings_cache()
    script = ScriptGenerator(get_settings(), fakes.LLM(script_json=_all_null_sfx_payload())).run(
        "R", data_brief, get_template("contrarian")
    )
    sfx = [s.sfx for s in script.scenes]
    assert any(sfx)  # no longer all-null
    assert script.scenes[0].sfx  # the opening always gets a sound
    assert script.scenes[2].sfx == "cash register"  # salary / six figures
    assert script.scenes[3].sfx == "wrong answer"  # biggest mistake
    lib = SfxLibrary("data/sounds")  # every assigned keyword maps to a shipped clip
    assert all(lib.resolve(kw) for kw in sfx if kw)


def test_sound_design_is_noop_when_disabled(monkeypatch, data_brief, fakes):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("SFX_ENABLED", "false")
    reset_settings_cache()
    script = ScriptGenerator(get_settings(), fakes.LLM(script_json=_all_null_sfx_payload())).run(
        "R", data_brief, get_template("contrarian")
    )
    assert all(s.sfx is None for s in script.scenes)


def test_sound_design_respects_model_authored_cues(monkeypatch, data_brief, fakes):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("SFX_ENABLED", "true")
    reset_settings_cache()
    payload = _all_null_sfx_payload()
    for sc in payload["scenes"]:  # the model designed plenty of sound itself
        sc["sfx"] = "pop"
    script = ScriptGenerator(get_settings(), fakes.LLM(script_json=payload)).run(
        "R", data_brief, get_template("contrarian")
    )
    assert [s.sfx for s in script.scenes] == ["pop"] * 5  # left untouched



