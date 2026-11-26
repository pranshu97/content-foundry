"""Regression suite for the 6 Systemic Architecture Bugs (post-Run 0050 audit)."""

from __future__ import annotations

from content_foundry.agents.research import Researcher
from content_foundry.agents.scene_image_director import (
    build_editorial_fallback_prompt,
    classify_setting_archetype,
    find_physical_text_leaks,
    has_human_presence,
    scrub_physical_text_cues,
)
from content_foundry.agents.script_generator import ScriptGenerator
from content_foundry.agents.storyboard_critic import (
    StoryboardCritic,
    validate_storyboard_deterministic,
)
from content_foundry.agents.visuals import _cut_pace, narration_windows
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.models import (
    Citation,
    DataBrief,
    KeyFact,
    Provenance,
    SceneCue,
    SceneTiming,
    Script,
    VoiceoverAsset,
    WordTiming,
    utcnow,
)
from content_foundry.pipeline.creator_data import extract_instruction_facts
from content_foundry.production.affiliate import candidate_platforms
from content_foundry.production.motion_graphics import (
    build_template_html,
    validate_template_props,
)
from content_foundry.production.seo import collect_embedded_sources
from content_foundry.production.visual_router import (
    TEMPLATE_FAMILY_OF,
    PlannedBeat,
    author_motion_graphics_props,
    beat_supports_evidence_card,
    build_planned_beats,
    route_visual_storyboard,
)
from content_foundry.providers.broll import _clip_ok, has_broll_cliche, score_broll_match
from content_foundry.providers.text_normalize import prepare_tts_text
from content_foundry.providers.tts import (
    _chunk_for_tts,
    _keep_slices,
    _trim_silence,
    split_tts_sentences,
)
from content_foundry.templates import get_template


# --------------------------------------------------------------------------- Bug 1
def test_bug1_templates_support_freeform_semantic_badges_and_explicit_polarity() -> None:
    # 1. rubric_checklist accepts free-form semantic status tags + explicit polarity (no interview lock).
    rub = validate_template_props(
        "rubric_checklist",
        {
            "variant": "checklist",
            "eyebrow": "SEVERANCE EXPOSURE MATRIX",
            "title": "WHO GETS TARGETED FIRST",
            "items": [
                {
                    "label": "Revenue-critical AI infrastructure pods",
                    "detail": "Direct GPU utilization ownership",
                    "status": "SHIELDED",
                    "polarity": "positive",
                },
                {
                    "label": "Middle-layer coordination roles",
                    "detail": "Flattened during Q4 re-org",
                    "status": "EXPOSED",
                    "polarity": "negative",
                },
                {
                    "label": " Statutory EU works-council regions",
                    "detail": "Delayed pending consultation",
                    "status": "DEFERRED",
                    "polarity": "neutral",
                },
            ],
            "source": None,
        },
    )
    assert [it["status"] for it in rub["items"]] == ["SHIELDED", "EXPOSED", "DEFERRED"]
    assert [it["tone"] for it in rub["items"]] == ["emerald", "crimson", "sky"]
    assert rub["source"] == ""
    rub_html = build_template_html("rubric_checklist", rub)
    assert "SEVERANCE EXPOSURE MATRIX" in rub_html
    assert "SHIELDED" in rub_html and "EXPOSED" in rub_html
    assert "STRONG HIRE" not in rub_html and "DOWNLEVEL" not in rub_html
    assert 'id="source-badge"' not in rub_html

    # 2. split_comparison auto-infers positive left / negative right when protected teams sit left!
    cmp_props = validate_template_props(
        "split_comparison",
        {
            "title": "RESTRUCTURING ASYMMETRY",
            "left_header": "PROTECTED CORE TEAMS",
            "right_header": "TARGETED RESTRUCTURING GROUPS",
            "left_items": ["Shielded principal infra engineers"],
            "right_items": ["Exposed legacy middle management"],
            "source": "null",
        },
    )
    assert cmp_props["left_polarity"] == "positive"
    assert cmp_props["right_polarity"] == "negative"
    assert cmp_props["source"] == ""
    cmp_html = build_template_html("split_comparison", cmp_props)
    assert '<span class="cmp-icon cmp-icon-l">&#10003;</span>' in cmp_html
    assert '<span class="cmp-icon cmp-icon-r">&#10005;</span>' in cmp_html
    assert 'id="source-badge"' not in cmp_html

    # 3. evidence_card never invents a fallback SOURCE // badge when source is None/empty.
    ev = validate_template_props(
        "evidence_card",
        {"publication": "SEC 10-Q FILING", "excerpt": "CapEx rose to $200B.", "source": None},
    )
    assert ev["source"] == ""
    assert 'id="source-badge"' not in build_template_html("evidence_card", ev)


# --------------------------------------------------------------------------- Bug 2
def test_bug2_comment_friction_binds_to_spoken_question_beat_and_caps_template_hold(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_PROGRAMMATIC_GRAPHICS", "true")
    monkeypatch.setenv("ENABLE_COMMENT_FRICTION_BEAT", "true")
    monkeypatch.setenv("MAX_TEMPLATE_DURATION_SEC", "8.0")
    monkeypatch.setenv("VIDEO_SPEED", "1.0")
    reset_settings_cache()
    s = get_settings()

    s6_narration = (
        "High performers treat voluntary exit packages as free call options because external offers "
        "arrive within weeks. Risk-averse mid-tier employees stay frozen in place hoping the next "
        "round skips their org. That adverse selection quietly drains the exact engineers leadership "
        "wanted to keep. Would you take an unsweetened buyout in this market? Drop your honest "
        "reasoning in the comments below."
    )
    script = Script(
        run_id="R50",
        template_id="contrarian",
        title_options=["Voluntary Exit Game Theory"],
        hook="Hyperscalers changed how layoffs work.",
        scenes=[
            SceneCue(
                index=0,
                narration="Hyperscalers shifted $200B into CapEx this year. Standard severance buyouts replaced mass WARN notices.",
                on_screen_text="CAPEX SHIFT",
                b_roll_keywords=["server building"],
            ),
            SceneCue(
                index=1,
                narration=s6_narration,
                on_screen_text="ADVERSE SELECTION",
                b_roll_keywords=["office hallway", "laptop desk", "team meeting"],
                is_comment_friction=True,
                discussion_question="Take buyout or stay put?",
            ),
        ],
        cta="Subscribe for more.",
        description="Game theory breakdown.",
        tags=["tech"],
        thumbnail_concept="Boardroom",
        thumbnail_text="TAKE THE BUYOUT?",
        word_count=90,
        provenance=Provenance(produced_by="test"),
    )
    words_s1 = s6_narration.split()
    wt = [
        WordTiming(
            word=w,
            start=250.0 + i * (36.0 / len(words_s1)),
            end=250.0 + (i + 1) * (36.0 / len(words_s1)),
        )
        for i, w in enumerate(words_s1)
    ]
    vo = VoiceoverAsset(
        run_id="R50",
        audio_path="assets/narration.mp3",
        duration_sec=286.0,
        sample_rate=22050,
        voice_id="v",
        provider="fake",
        word_timings=wt,
        scene_timings=[
            SceneTiming(scene_index=0, start=0.0, end=12.0),
            SceneTiming(scene_index=1, start=250.0, end=286.0),  # 36s scene with only 3 keywords!
        ],
        provenance=Provenance(produced_by="test"),
    )

    beats = build_planned_beats(
        script, vo, s, narration_windows_fn=narration_windows, cut_pace_fn=_cut_pace
    )
    routed = route_visual_storyboard(beats, s, video_title="Voluntary Exit Game Theory")

    # Every beat stays within max_template_duration_sec (8.0s + small rounding tolerance).
    assert all(b.viewer_duration <= 8.35 for b in routed)

    # Comment Friction binds strictly to the beat containing the spoken question, NOT shot_index 0!
    cf_beats = [b for b in routed if b.is_comment_friction]
    assert len(cf_beats) == 1
    cf_b = cf_beats[0]
    assert cf_b.scene_index == 1
    assert cf_b.shot_index > 0
    assert "Would you take an unsweetened buyout" in cf_b.spoken_text
    assert cf_b.visual_type == "programmatic_graphic"
    assert cf_b.template_id == "rubric_checklist"
    assert (cf_b.template_props or {}).get("variant") == "discussion_prompt"


# --------------------------------------------------------------------------- Bug 3
def test_bug3_broll_cliche_backdoor_closed_and_opening_forbids_ping_pong(monkeypatch) -> None:
    vocab = {"server", "data", "center", "financial", "chart", "stock", "market", "engineer"}
    # Neon server tunnel & stock candlestick clips are blocked even when sharing vocab words.
    assert has_broll_cliche("modern hyperscaler data center", "neon, glowing, server, tunnel")
    assert has_broll_cliche("financial stock chart climbing", "candlestick, trading, green")
    assert (
        _clip_ok(
            "hyperscaler data center",
            "neon, server, tunnel, data",
            vocab,
        )
        is False
    )
    assert (
        score_broll_match(
            "financial stock chart climbing",
            "stock, chart, candlestick, market",
            vocab,
        )
        == 0.0
    )

    # Opening 0:00-0:30 neither over-chops 4 sentences into 7 duplicate cards nor ping-pongs
    # split_comparison <-> rubric_checklist back-to-back.
    monkeypatch.setenv("ENABLE_PROGRAMMATIC_GRAPHICS", "true")
    monkeypatch.setenv("BAN_KEN_BURNS_IN_OPENING", "true")
    reset_settings_cache()
    s = get_settings()

    opening_narr = (
        "Tech giants pledged $200B toward AI data centers while freezing headcount. "
        "Voluntary exit offers replaced mass layoffs across 60,000 roles. "
        "Managers framed buyouts as flexibility rather than balance-sheet triage. "
        "Here is how the severance math actually works behind closed doors."
    )
    script = Script(
        run_id="R50B",
        template_id="contrarian",
        title_options=["Severance Math"],
        hook="Tech giants pledged $200B.",
        scenes=[
            SceneCue(
                index=0,
                narration=opening_narr,
                on_screen_text="THE $200B SHIFT",
                b_roll_keywords=["office laptop", "meeting room", "desk"],
            )
        ],
        cta="Subscribe.",
        description="Desc.",
        tags=["tech"],
        thumbnail_concept="Desk",
        thumbnail_text="$200B SHIFT",
        word_count=45,
        provenance=Provenance(produced_by="test"),
    )
    vo = VoiceoverAsset(
        run_id="R50B",
        audio_path="assets/narration.mp3",
        duration_sec=28.0,
        sample_rate=22050,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[SceneTiming(scene_index=0, start=0.0, end=28.0)],
        provenance=Provenance(produced_by="test"),
    )
    beats = build_planned_beats(
        script, vo, s, narration_windows_fn=narration_windows, cut_pace_fn=_cut_pace
    )
    assert len(beats) == 4  # 4 distinct sentences -> 4 distinct beats, NOT 7!
    routed = route_visual_storyboard(beats, s, video_title="Severance Math")
    tpls = [b.template_id for b in routed if b.visual_type == "programmatic_graphic"]
    for a, b_tid in zip(tpls, tpls[1:], strict=False):
        assert a != b_tid
        assert not (
            TEMPLATE_FAMILY_OF.get(a or "") == "comparison_rubric"
            and TEMPLATE_FAMILY_OF.get(b_tid or "") == "comparison_rubric"
        )


# --------------------------------------------------------------------------- Bug 4
def test_bug4_video_wide_setting_cap_human_presence_and_zero_physical_text(monkeypatch) -> None:
    monkeypatch.setenv("MAX_SETTING_ARCHETYPE_REPEATS", "2")
    monkeypatch.setenv("MIN_HUMAN_PRESENCE_RATIO", "0.35")
    reset_settings_cache()
    s = get_settings()

    # Physical text leak detector catches printed ledgers, embossed seals, engraved labels, quotes,
    # while ignoring the style bible's own "zero legible printed text" negative clause.
    leaks = find_physical_text_leaks(
        'Macro shot of a printed ledger with document header reading "SEVERANCE" and laser-etched plate, zero legible printed text.'
    )
    assert len(leaks) >= 3
    scrubbed = scrub_physical_text_cues(
        'Macro shot of a printed document reading "CONFIDENTIAL" beside an embossed seal and laser-etched plate.'
    )
    assert find_physical_text_leaks(scrubbed) == []
    assert "CONFIDENTIAL" not in scrubbed

    assert classify_setting_archetype("sunlit empty corporate conference room") == (
        "office_conference_room"
    )
    assert classify_setting_archetype("quiet law library with oak bookshelves") == "library_archive"

    # Build 6 AI beats where 3 use office_conference_room (exceeds cap=2) and 0 have humans (<0.35).
    beats: list[PlannedBeat] = [
        PlannedBeat(
            global_index=0,
            scene_index=0,
            shot_index=0,
            audio_start=0.0,
            audio_end=6.0,
            audio_duration=6.0,
            viewer_start=0.0,
            viewer_end=6.0,
            viewer_duration=6.0,
            spoken_text="Hook stat 31%.",
            beat_query="stat",
            visual_type="programmatic_graphic",
            template_id="big_stat_card",
            template_props={"value": "31%", "label": "Drop"},
        )
    ]
    settings_seq = [
        ("daylit boardroom", "wide_environment"),
        ("hardware test bench", "medium_human_action"),
        ("server colocation aisle", "tight_physical_detail"),
        ("executive conference room", "wide_environment"),
        ("industrial verification lab", "medium_human_action"),
        ("empty corner office", "tight_physical_detail"),  # 3rd office_conference_room!
    ]
    for idx, (st_name, sh_type) in enumerate(settings_seq, start=1):
        t0 = 30.0 + idx * 6.0
        beats.append(
            PlannedBeat(
                global_index=idx,
                scene_index=1,
                shot_index=idx,
                audio_start=t0,
                audio_end=t0 + 6.0,
                audio_duration=6.0,
                viewer_start=t0,
                viewer_end=t0 + 6.0,
                viewer_duration=6.0,
                spoken_text=f"Analytical point {idx} about restructuring.",
                beat_query="workspace",
                visual_type="ai_image",
                shot_type=sh_type,
                prompt=f"Clean 35mm shot in a {st_name}, focusing on a closed folder, zero neon glow, zero floating UI text.",
                visual_meta={
                    "subject": f"artifact {idx}",
                    "setting": st_name,
                    "shot_type": sh_type,
                    "dominant_objects": ["folder"],
                },
            )
        )

    _, s_issues = validate_storyboard_deterministic(beats, s)
    # Beat 6 caught for 3rd office_conference_room; medium_human_action beats caught for 0 humans!
    assert any("archetype cap" in msg for msg in s_issues.get(6, []))
    assert any("human presence required" in msg for msgs in s_issues.values() for msg in msgs)

    # StoryboardCritic repairs both deterministically so final report is clean!
    report = StoryboardCritic(s, llm_provider=None).audit_and_repair(
        beats, regenerate_shot_fn=None, video_title="Test"
    )
    assert report.ok is True
    ai_after = [b for b in beats if b.visual_type == "ai_image"]
    human_share = sum(1 for b in ai_after if has_human_presence(b.prompt, b.visual_meta)) / len(
        ai_after
    )
    assert human_share >= 0.35

    # Editorial fallback never repeats >2 of any archetype across 8 consecutive shots.
    hist: list[dict] = []
    for k in range(8):
        fb = build_editorial_fallback_prompt(
            f"Point {k}",
            ("wide_environment", "medium_human_action", "tight_physical_detail")[k % 3],
            s,
            visual_history=hist,
        )
        hist.append(fb.visual_meta)
    arch_cts: dict[str, int] = {}
    for h in hist:
        a = h["setting_archetype"]
        arch_cts[a] = arch_cts.get(a, 0) + 1
    assert max(arch_cts.values()) <= 2


# --------------------------------------------------------------------------- Bug 5
def test_bug5_instruction_facts_preserved_verified_sources_only_and_strict_evidence_card(
    monkeypatch, fakes
) -> None:
    # 1. Hard numbers in --instructions become creator_supplied KeyFacts (never stripped, no fake source).
    instr = (
        "Cover how Meta France severance hits 2.15 months per year of service while Google "
        "Switzerland reaches 12 months. Contrast that with $195B to $205B in 2026 AI CapEx. "
        "End by asking viewers whether they would sign an unsweetened VEO."
    )
    facts = extract_instruction_facts(instr)
    assert len(facts) == 2
    assert all(f.creator_supplied and f.citation.source == "" for f in facts)

    # 2. Universal monetization (Fenzo AI) stays active even when --instructions carries a custom CTA!
    monkeypatch.setenv("AFFILIATE_ENABLED", "true")
    monkeypatch.setenv("AFFILIATE_FENZO_URL", "https://fenzo.ai/?ref=TEST")
    reset_settings_cache()
    s = get_settings()
    cands = candidate_platforms(
        s, tags=["corporate layoffs", "severance buyout"], niche="tech industry"
    )
    assert [c.label for c in cands] == ["Fenzo AI"]

    # 3. Low-tier domains (amazingworkplaces.co) filtered from Researcher, ScriptGenerator, and SEO Sources;
    #    hallucinated visual_source strings ("Tech Workforce Game Theory Matrix") stripped!
    brief = DataBrief(
        run_id="R5",
        niche="tech careers",
        key_facts=[
            *facts,
            KeyFact(
                statement="60,000 employees received voluntary exit offers.",
                citation=Citation(
                    source="search",
                    url="https://techcrunch.com/2026/10/microsoft-veo/",
                    snippet="60,000 eligible",
                    observed_at=utcnow(),
                ),
            ),
            KeyFact(
                statement="Workplace perks shifted in 2026.",
                citation=Citation(
                    source="search",
                    url="https://amazingworkplaces.co/perks-2026/",
                    snippet="low tier blog",
                    observed_at=utcnow(),
                ),
            ),
        ],
        coverage={"search": True},
        provenance=Provenance(produced_by="test"),
    )

    class _LowTierSearch:
        def search(self, query, max_results=5):
            from collections import namedtuple

            Hit = namedtuple("Hit", "url title snippet")
            return [
                Hit("https://amazingworkplaces.co/post", "Blog", "snippet"),
                Hit("https://www.reuters.com/technology/capex/", "Reuters", "CapEx rose"),
            ]

    res_brief = Researcher(
        s,
        fakes.LLM(
            script_json={
                "points": [
                    {
                        "point": "CapEx rose sharply",
                        "explanation": "GPU clusters",
                        "evidence": "$200B",
                        "source_url": "https://www.reuters.com/technology/capex/",
                    }
                ]
            }
        ),
        search_provider=_LowTierSearch(),
    ).run("R5", brief, idea="Tech Layoffs")
    assert all("amazingworkplaces.co" not in u for u in res_brief.source_urls)

    draft_payload = {
        "title_options": ["Why Buyouts Replaced Layoffs"],
        "hook": "Meta France severance hits 2.15 months per year.",
        "scenes": [
            {
                "index": 0,
                "narration": "Meta France severance hits 2.15 months per year while Google Switzerland reaches 12 months.",
                "on_screen_text": "EU SEVERANCE MATH",
                "b_roll_keywords": ["courthouse"],
                "fact_ref": 0,  # creator_supplied instruction fact -> keep numbers, NO fake source!
                "visual_source": "International Labor Code Analysis",
            },
            {
                "index": 1,
                "narration": "Roughly 60,000 employees received standard voluntary exit offers.",
                "on_screen_text": "ELIGIBLE POOL",
                "b_roll_keywords": ["campus"],
                "fact_ref": 2,  # verified techcrunch.com
                "visual_source": None,
            },
            {
                "index": 2,
                "narration": "Pure game theory explains why top performers take the cash first.",
                "on_screen_text": "ADVERSE SELECTION",
                "b_roll_keywords": ["chess"],
                "fact_ref": None,
                "visual_source": "Tech Workforce Game Theory Matrix",  # hallucinated!
            },
            {
                "index": 3,
                "narration": "Workplace perks shifted 10% in 2026.",
                "on_screen_text": "PERKS",
                "b_roll_keywords": ["desk"],
                "fact_ref": 3,  # amazingworkplaces.co -> low-tier blocked!
                "visual_source": "amazingworkplaces.co",
            },
        ],
        "cta": "Drop your take below.",
        "description": "Deep dive.",
        "tags": ["layoffs"],
        "thumbnail_concept": "Desk",
        "thumbnail_text": "BUYOUT TRAP",
    }
    gen_script = ScriptGenerator(s, fakes.LLM(script_json=draft_payload)).run(
        "R5", brief, get_template("contrarian"), instructions=instr
    )
    # Creator's 2.15 and 12 numbers survived _repair_grounding, AND fake source was stripped!
    assert "2.15" in gen_script.scenes[0].narration
    assert gen_script.scenes[0].visual_source is None
    assert "Source:" not in (gen_script.scenes[0].on_screen_text or "")
    # Verified TechCrunch source preserved!
    assert gen_script.scenes[1].visual_source == "techcrunch.com"
    # Hallucinated "Tech Workforce Game Theory Matrix" & low-tier "amazingworkplaces.co" stripped!
    assert gen_script.scenes[2].visual_source is None
    assert gen_script.scenes[3].visual_source is None
    embedded = collect_embedded_sources(gen_script, brief=brief)
    assert [u for _, u in embedded] == ["https://techcrunch.com/2026/10/microsoft-veo/"]

    # 4. Unverified beat cannot use evidence_card and LLM prop director cannot inject fake source.
    unverified_beat = PlannedBeat(
        global_index=2,
        scene_index=2,
        shot_index=0,
        audio_start=40.0,
        audio_end=46.0,
        audio_duration=6.0,
        viewer_start=40.0,
        viewer_end=46.0,
        viewer_duration=6.0,
        spoken_text="Pure game theory explains why top performers take the cash first.",
        scene_narration="Pure game theory explains why top performers take the cash first.",
        beat_query="corridor",
        visual_source="",
        visual_type="programmatic_graphic",
        template_id="split_comparison",
        template_props={},
    )
    assert beat_supports_evidence_card(unverified_beat, s) is False
    fake_dir_llm = fakes.LLM(
        script_json={
            "graphics": [
                {
                    "global_index": 2,
                    "template_id": "split_comparison",
                    "props": {
                        "title": "GAME THEORY",
                        "left_header": "STAY",
                        "right_header": "EXIT",
                        "left_items": ["Capped upside"],
                        "right_items": ["Free option value"],
                        "source": "Tech Workforce Game Theory Matrix",
                    },
                }
            ]
        }
    )
    author_motion_graphics_props([unverified_beat], s, fake_dir_llm)
    assert (unverified_beat.template_props or {}).get("source") == ""


# --------------------------------------------------------------------------- Bug 6
def test_bug6_sentence_year_compound_safe_tts_and_padded_micro_fade_trimmer() -> None:
    import torch

    raw = (
        "Do not confuse standard VEO packages with the April Rule-of-70 program. "
        "In October 2026 Microsoft sent offers to 60,000-person U.S. teams, "
        "while Meta France averaged 2.15 months per year continuously."
    )
    spoken = prepare_tts_text(raw)
    # Natural calendar year ("twenty twenty six"), zero hyphens anywhere, clean V E O acronym!
    assert "twenty twenty six" in spoken
    assert "two thousand and twenty" not in spoken
    assert "Rule of seventy" in spoken
    assert "sixty thousand person" in spoken
    assert "two point one five" in spoken
    assert "-" not in spoken

    # Decimal 2.15 and abbreviation U.S. never trigger a false mid-sentence split!
    sents = split_tts_sentences(
        "In the U.S. Meta France paid 2.15 months per year. Dr. Smith agreed."
    )
    assert sents == [
        "In the U.S. Meta France paid 2.15 months per year.",
        "Dr. Smith agreed.",
    ]

    chunks = _chunk_for_tts(spoken, max_chars=250, single_sentence=True)
    assert len(chunks) == 2
    assert chunks[0].endswith("program.")
    assert chunks[1].startswith("In October twenty twenty six")

    # Separate head (55) vs tail (85) safety padding in _keep_slices:
    keep = _keep_slices(1000, [(0, 200), (700, 1000)], pad=50, max_gap=0, edge_pad=55, tail_pad=85)
    assert keep == [(145, 785)]  # 200 - 55 = 145 head start; 700 + 85 = 785 tail end!

    # Quiet "-ly" tail at amplitude 0.011 (between old 0.015 and new 0.008) is preserved + 85ms pad + 15ms micro-fade!
    sr = 2000
    wav = torch.zeros((1, 2000), dtype=torch.float32)
    wav[0, 400:1000] = 0.25  # loud stem ("continuous")
    wav[0, 1000:1300] = 0.011  # quiet "-ly" syllable that old thresh=0.015 clipped!
    trimmed = _trim_silence(
        wav,
        sr,
        edge_pad_ms=55,
        tail_pad_ms=85,
        micro_fade_ms=15,
    )
    # Expected kept span: [400 - 110 = 290 .. 1300 + 170 = 1470] = 1180 samples (vs 880 if "-ly" clipped!)
    assert int(trimmed.shape[-1]) == 1180
    # Micro-fade tapers endpoints smoothly to 0.0 at cut boundaries without zeroing the "-ly" body:
    assert float(trimmed[0, 0].abs()) == 0.0
    assert float(trimmed[0, -1].abs()) == 0.0
    assert float(trimmed[0, 1000 - 290 + 50].abs()) > 0.009
