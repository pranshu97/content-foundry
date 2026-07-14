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


def test_revision_embeds_previous_draft_for_surgical_edit(settings, data_brief, fakes):
    # A revision must hand the model its OWN previous draft to EDIT — not regenerate from scratch,
    # which is what made the loop lose a good ending/wit between attempts.
    llm = fakes.LLM()
    prev = ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    ScriptGenerator(settings, llm).run(
        "R", data_brief, get_template("contrarian"),
        judge_feedback="- WITTINESS 2.5/10: too dry.", previous_script=prev,
    )
    system = llm.calls[-1]["system"]
    assert "PREVIOUS DRAFT" in system and "improve THAT draft" in system
    assert "bottom rung is gone" in system  # a distinctive line from the prior draft, verbatim
    assert "WITTINESS 2.5/10" in system  # the fix-list is included


def test_revision_without_previous_draft_falls_back(settings, data_brief, fakes):
    llm = fakes.LLM()
    ScriptGenerator(settings, llm).run(
        "R", data_brief, get_template("contrarian"),
        judge_feedback="- ENDING 0.0/10: add a close.",
    )
    system = llm.calls[-1]["system"]
    assert "REVISION" in system and "ENDING 0.0/10" in system
    assert "PREVIOUS DRAFT" not in system  # nothing to edit -> old single-shot behavior


def test_duplicate_scenes_are_dropped_deterministically(settings, data_brief, fakes):
    # A padded draft that recycles a scene must have the copy REMOVED in code (a hard guarantee),
    # not merely flagged for the model to fix — that detection alone let dupes recur (run 0010).
    dup = "Everyone says just grind leetcode, but entry postings actually thinned out this past year."
    payload = {
        "title_options": ["t"],
        "hook": "A specific hook about breaking into big tech right now.",
        "scenes": [
            {"index": 0, "narration": dup, "fact_ref": 0},
            {"index": 1, "narration": "Median pay for these roles still sits high, which surprises most applicants today.", "fact_ref": 1},
            {"index": 2, "narration": "Target adjacent teams first and ship one small portfolio project this week to stand out.", "fact_ref": None},
            {"index": 3, "narration": "Recruiters skim fast, so lead with measurable outcomes and the exact tools you used.", "fact_ref": None},
            {"index": 4, "narration": dup, "fact_ref": 0},  # near-identical to scene 0 -> dropped
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0, 1],
    }
    llm = fakes.LLM(script_json=payload)
    script = ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    narrations = [s.narration for s in script.scenes]
    assert narrations.count(dup) == 1  # the recycled scene was removed in code
    assert [s.index for s in script.scenes] == list(range(len(script.scenes)))  # re-indexed


def test_research_context_appears_in_prompt_when_provided(settings, data_brief, fakes):
    from content_foundry.models import ResearchBrief, ResearchPoint

    llm = fakes.LLM()
    research = ResearchBrief(run_id="R", idea="x", points=[
        ResearchPoint(point="ATS scans keywords", explanation="the first reader cannot gauge skill",
                      evidence="most resumes are filtered", source_url="https://x/1"),
    ])
    ScriptGenerator(settings, llm).run(
        "R", data_brief, get_template("contrarian"), research=research)
    system = llm.calls[-1]["system"]
    assert "RESEARCH (source-backed depth" in system  # the depth block header is rendered
    assert "ATS scans keywords" in system and "cannot gauge skill" in system


def test_research_context_absent_when_none(settings, data_brief, fakes):
    llm = fakes.LLM()
    ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    assert "RESEARCH (source-backed depth" not in llm.calls[-1]["system"]  # no block without research


def test_ending_is_guaranteed_when_model_omits_it(settings, data_brief, fakes):
    from content_foundry.agents.judge_checks import ending_report

    # The model closes with plain advice — NO subscribe nudge and NO sign-off.
    payload = {
        "title_options": ["t"],
        "hook": "A specific hook about breaking into big tech right now.",
        "scenes": [
            {"index": 0, "narration": "First, target adjacent teams and build a small portfolio project.",
             "fact_ref": 0},
            {"index": 1, "narration": "Second, referrals matter far more than cold applications do.",
             "fact_ref": 1},
            {"index": 2, "narration": "Finally, drill the interview format until it feels routine and calm.",
             "fact_ref": None},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0, 1],
    }
    llm = fakes.LLM(script_json=payload)
    script = ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    # the ending floor is now GUARANTEED in code — both a nudge and a sign-off were appended
    assert ending_report(script)[0] == 10.0
    assert "subscribe" in script.scenes[-1].narration.lower()
    assert "next one" in script.scenes[-1].narration.lower()


def test_intro_tagline_prepended_to_first_scene(monkeypatch, data_brief, fakes):
    # A fixed channel intro must open every video (spoken first), guaranteed in code regardless of
    # what the model wrote — and only on the FIRST scene.
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("INTRO_ENABLED", "true")
    monkeypatch.setenv("INTRO_TAGLINE", "No fluff, let's dive in.")
    reset_settings_cache()
    settings = get_settings()
    script = ScriptGenerator(settings, fakes.LLM()).run("R", data_brief, get_template("contrarian"))
    assert script.scenes[0].narration.startswith("No fluff, let's dive in. ")
    assert script.scenes[1].narration.count("No fluff") == 0  # only the opening scene carries it


def test_intro_tagline_not_doubled_when_already_present(monkeypatch, data_brief, fakes):
    # A revision embeds the previous (already-introed) draft, so the prepend must be idempotent.
    from content_foundry.config import get_settings, reset_settings_cache

    tag = "No fluff, let's dive in."
    monkeypatch.setenv("INTRO_ENABLED", "true")
    monkeypatch.setenv("INTRO_TAGLINE", tag)
    reset_settings_cache()
    settings = get_settings()
    payload = {
        "title_options": ["t"],
        "hook": "A specific hook about breaking into big tech right now.",
        "scenes": [
            {"index": 0, "narration": f"{tag} Everyone says grind leetcode, but entry roles thinned this year.", "fact_ref": 0},
            {"index": 1, "narration": "Referrals beat cold applications because a human vouches before the resume is even read.", "fact_ref": 1},
            {"index": 2, "narration": "So target adjacent teams first, then ship one portfolio project. If this helped, subscribe, and I'll see you in the next one.", "fact_ref": None},
        ],
        "cta": "x", "description": "uses synthetic content", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0, 1],
    }
    script = ScriptGenerator(settings, fakes.LLM(script_json=payload)).run(
        "R", data_brief, get_template("contrarian"))
    assert script.scenes[0].narration.lower().count("no fluff, let's dive in.") == 1  # not doubled
    assert script.scenes[0].narration.startswith(tag)


def test_reformat_retry_on_bad_json(settings, data_brief, fakes):
    llm = fakes.LLM(bad_then_good=True)
    script = ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    assert llm.call_count == 2  # one bad + one reformat retry
    assert script.hook


def test_creator_bio_stays_generic_when_unset():
    # Both blank => an EMPTY clause, so the shipped prompt stays fully generic for anyone who doesn't
    # set one. A set bio/tag produces a "use sparingly" credibility clause for narration + titles.
    from content_foundry.agents.script_generator import _creator_context

    assert _creator_context("") == ""
    assert _creator_context("   ", "  ") == ""
    clause = _creator_context("AI Scientist at Microsoft", "FAANG AI Scientist")
    assert "AI Scientist at Microsoft" in clause  # narration authority
    assert "FAANG AI Scientist" in clause  # title/thumbnail credibility tag
    assert "SPARINGLY" in clause  # woven in subtly, never a brag


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


def test_no_ai_disclaimer_injected_into_description(settings, data_brief, fakes):
    # The generator must NOT inject an AI/synthetic-content note into the description; the LLM's own
    # description is preserved as-is (disclosure is a metadata flag only, not description text).
    payload = {
        "title_options": ["t"], "hook": "Specific 31% hook.",
        "scenes": [{"index": 0, "narration": "Postings fell 31%.", "on_screen_text": None,
                    "b_roll_keywords": [], "fact_ref": 0}],
        "cta": "x", "description": "No disclosure here.", "tags": [], "thumbnail_concept": "x",
        "grounded_fact_refs": [0],
    }
    llm = fakes.LLM(script_json=payload)
    script = ScriptGenerator(settings, llm).run("R", data_brief, get_template("contrarian"))
    assert "synthetic" not in script.description.lower()
    assert "No disclosure here." in script.description


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


def test_search_source_citation_shows_website_domain():
    # A web-search fact has no fixed label; the on-screen source should be the site domain, not "Search".
    from datetime import UTC, datetime

    from content_foundry.agents.script_generator import _source_label
    from content_foundry.models.data_brief import Citation

    now = datetime.now(UTC)
    web = Citation(source="search", url="https://online.msoe.edu/engineering/blog/ml-careers",
                   observed_at=now, snippet="x")
    assert _source_label(web) == "online.msoe.edu"
    assert _source_label(Citation(source="search", url="https://www.techcrunch.com/ai",
                                  observed_at=now, snippet="x")) == "techcrunch.com"  # www. stripped
    assert _source_label(Citation(source="adzuna", observed_at=now, snippet="x")) == "Adzuna"


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


def test_clean_narration_softens_structural_labels():
    from content_foundry.agents.script_generator import _clean_narration

    # A model that announces its explanation structure out loud ("Why this works:") gets the label
    # dropped and the sentence after it kept, so narration doesn't read like a lecture outline.
    assert (
        _clean_narration("Why this works: recruiters scan for the exact keywords.")
        == "Recruiters scan for the exact keywords."
    )
    assert (
        _clean_narration("First, tailor it. Here's how it works: the ATS ranks you.")
        == "First, tailor it. The ATS ranks you."
    )
    assert (
        _clean_narration("Step 1: mirror the posting's language.")
        == "Mirror the posting's language."
    )
    # The same words used naturally (not as a heading) are left completely alone.
    assert (
        _clean_narration("That is exactly why this works so well in practice.")
        == "That is exactly why this works so well in practice."
    )


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


def test_replace_em_dashes_uses_commas():
    from content_foundry.agents.script_generator import _replace_em_dashes

    assert (
        _replace_em_dashes("The truth \u2014 and it's wild \u2014 is simple.")
        == "The truth, and it's wild, is simple."
    )
    assert _replace_em_dashes("Wait\u2014what?") == "Wait, what?"
    assert _replace_em_dashes("One thing matters -- grit.") == "One thing matters, grit."
    assert _replace_em_dashes("\u2014 really?") == "really?"  # leading comma dropped
    # A normal hyphen (well-known) is never touched.
    assert _replace_em_dashes("A well-known, state-of-the-art tool.") == (
        "A well-known, state-of-the-art tool."
    )
    assert _replace_em_dashes(None) is None


def test_em_dashes_never_survive_a_run(settings, data_brief, fakes):
    d = "\u2014"  # em dash
    payload = {
        "title_options": [f"The Truth {d} Revealed"],
        "hook": f"Here is the shocking truth {d} recruiters skim resumes incredibly fast.",
        "scenes": [
            {"index": 0,
             "narration": f"Tailor your resume {d} every single bullet point {d} to the job you want.",
             "on_screen_text": f"Do this {d} now", "b_roll_keywords": ["resume"],
             "fact_ref": None, "sfx": None},
            {"index": 1,
             "narration": f"Recruiters are busy {d} so lead with your strongest, most relevant wins.",
             "on_screen_text": None, "b_roll_keywords": ["office"], "fact_ref": None, "sfx": None},
            {"index": 2,
             "narration": f"Keep it clean {d} one page, clear sections, and real measurable impact.",
             "on_screen_text": None, "b_roll_keywords": ["laptop"], "fact_ref": None, "sfx": None},
        ],
        "cta": f"Subscribe {d} you won't regret it.",
        "description": f"A guide {d} with real data {d} for you. Uses synthetic content.",
        "tags": [], "thumbnail_concept": f"Bold text {d} high contrast",
        "grounded_fact_refs": [],
    }
    script = ScriptGenerator(settings, fakes.LLM(script_json=payload)).run(
        "R", data_brief, get_template("contrarian")
    )
    parts = [
        script.hook, script.cta, script.description, script.thumbnail_concept,
        *script.title_options,
        *(s.narration for s in script.scenes),
        *(s.on_screen_text or "" for s in script.scenes),
    ]
    assert d not in " ".join(parts)  # the hard rule holds across every field
    assert script.scenes[0].narration == (
        "Tailor your resume, every single bullet point, to the job you want."
    )




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



