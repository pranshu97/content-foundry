"""Comprehensive unit & schema tests for Workstreams 0-5 (config, router, templates, anti-collapse
director, storyboard critic, script hygiene, and procedural studio audio)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from content_foundry.agents.judge_checks import average_sentence_words, comment_friction_report
from content_foundry.agents.renderer import Renderer
from content_foundry.agents.scene_image_director import (
    DualPromptResult,
    SceneImageDirector,
    build_bidirectional_window,
    find_cliche_hits,
    format_visual_history,
    scrub_cliche_phrases,
)
from content_foundry.agents.script_generator import ScriptGenerator, _offload_spoken_citation
from content_foundry.agents.storyboard_critic import (
    StoryboardCritic,
    validate_storyboard_deterministic,
)
from content_foundry.config import MOTION_TEMPLATE_IDS, Settings, get_settings, reset_settings_cache
from content_foundry.errors import ConfigError
from content_foundry.models import (
    Citation,
    DataBrief,
    KeyFact,
    Provenance,
    SceneTiming,
    SceneVisual,
    VisualPackage,
    VisualShot,
    VoiceoverAsset,
    WordTiming,
    utcnow,
)
from content_foundry.production.motion_graphics import (
    build_template_html,
    clamp_text,
    clamp_words,
    validate_template_props,
)
from content_foundry.production.sound_design import (
    ensure_dark_ambient_wav,
    ensure_transition_sfx_wav,
    mix_studio_audio,
)
from content_foundry.production.timeline import build_timeline
from content_foundry.production.visual_router import (
    SHOT_TYPE_CYCLE,
    PlannedBeat,
    classify_beat_override,
    pick_enabled_template,
    route_visual_storyboard,
)
from content_foundry.prompts import load_prompt
from content_foundry.providers.broll import PixabayBrollClient, moment_terms, score_broll_match
from content_foundry.templates import get_template

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------- Workstream 0: Config & Env Parity
def test_env_examples_cover_every_settings_field_with_zero_drift() -> None:
    model_keys = set(Settings.model_fields.keys())
    for rel in (".env.example", "src/content_foundry/data/env.example"):
        env_keys = set()
        for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                env_keys.add(s.split("=", 1)[0].strip().lower())
        assert model_keys - env_keys == set(), f"{rel} missing keys: {model_keys - env_keys}"
        assert env_keys - model_keys == set(), f"{rel} stale keys: {env_keys - model_keys}"


def test_every_settings_field_is_referenced_in_source() -> None:
    """Guard against dead config variables whose toggle does nothing."""
    src_root = ROOT / "src" / "content_foundry"
    blobs: list[str] = []
    for py in src_root.rglob("*.py"):
        if py.name == "config.py":
            # Exclude field declaration lines (`    field_name: type = ...`) so a field must be read
            # either in a config property/validator or in another module.
            kept = [
                ln
                for ln in py.read_text(encoding="utf-8").splitlines()
                if not ln.startswith("    ") or ":" not in ln.split("=", 1)[0]
            ]
            # Keep method/property bodies inside config.py (indented 8+ spaces).
            method_lines = [
                ln
                for ln in py.read_text(encoding="utf-8").splitlines()
                if ln.startswith("        ")
            ]
            blobs.append("\n".join(kept + method_lines))
        else:
            blobs.append(py.read_text(encoding="utf-8"))
    corpus = "\n".join(blobs)
    dead = [name for name in Settings.model_fields if name not in corpus]
    assert dead == [], f"Dead Settings fields read nowhere: {dead}"


def test_config_cross_field_validators_reject_inverted_bounds(monkeypatch) -> None:
    monkeypatch.setenv("MIN_PROGRAMMATIC_RATIO", "0.50")
    monkeypatch.setenv("TARGET_PROGRAMMATIC_RATIO", "0.30")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()

    monkeypatch.setenv("MIN_PROGRAMMATIC_RATIO", "0.20")
    monkeypatch.setenv("OPENING_BEAT_MIN_SEC", "6.0")
    monkeypatch.setenv("OPENING_BEAT_MAX_SEC", "3.0")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()

    monkeypatch.setenv("OPENING_BEAT_MIN_SEC", "3.0")
    monkeypatch.setenv("OPENING_BEAT_MAX_SEC", "5.0")
    monkeypatch.setenv("PROGRAMMATIC_ANCHOR_MIN_SEC", "70.0")
    monkeypatch.setenv("PROGRAMMATIC_ANCHOR_MAX_SEC", "50.0")
    reset_settings_cache()
    with pytest.raises(ConfigError):
        get_settings()


def test_per_template_controls_disable_and_weight(monkeypatch) -> None:
    monkeypatch.setenv("TPL_BIG_STAT_CARD_ENABLED", "false")
    monkeypatch.setenv("TPL_CODE_IDE_MOCKUP_WEIGHT", "3.5")
    reset_settings_cache()
    s = get_settings()
    enabled = s.enabled_motion_templates
    assert "big_stat_card" not in enabled
    assert enabled["code_ide_mockup"][0] == 3.5
    chosen = pick_enabled_template("big_stat_card", s)
    assert chosen is not None and chosen != "big_stat_card"
    with pytest.raises(ConfigError):
        s.template_spec("nonexistent_template")


# --------------------------------------------------------- Workstream 2: Templates & Router
@pytest.mark.parametrize("template_id", MOTION_TEMPLATE_IDS)
def test_all_seven_templates_validate_props_and_escape_html(template_id: str) -> None:
    noisy = {
        "value": "-42.5%",
        "label": "Long headline <script>alert(1)</script> " * 6,
        "excerpt": "Report shows <p99> & tail latency dropped 42.5% across 100 clusters. " * 4,
        "code_lines": ["if latency < 15.0 and score >= 0.85:  # <fast> & clean"],
        "source": "CRA Survey & Benchmark",
    }
    norm = validate_template_props(template_id, noisy, max_chars=80)
    assert isinstance(norm, dict)
    doc = build_template_html(template_id, noisy, width=1920, height=1080, max_chars=80)
    assert "<script>alert(1)</script>" not in doc
    assert "window.__setFrame" in doc
    assert "window.__inspectLayout" in doc


def test_clamp_helpers_preserve_word_boundaries() -> None:
    out = clamp_text("Distributed systems trade tail latency against coordination overhead", 34)
    assert len(out) <= 34 and out.endswith("...")
    assert (
        clamp_words("Would you pass or reject this architecture?", 5) == "Would you pass or reject"
    )


def test_override_classifier_maps_data_code_architecture_rubric_and_comparison(settings) -> None:
    def _beat(line: str, *, cf: bool = False) -> PlannedBeat:
        return PlannedBeat(
            global_index=0,
            scene_index=1,
            shot_index=0,
            audio_start=35.0,
            audio_end=40.0,
            audio_duration=5.0,
            viewer_start=35.0,
            viewer_end=40.0,
            viewer_duration=5.0,
            spoken_text=line,
            beat_query="engineer desk",
            is_comment_friction=cf,
            discussion_question="Fair bar or broken?" if cf else "",
        )

    assert classify_beat_override(_beat("Postings dropped 31% this quarter."), settings) == (
        True,
        "big_stat_card",
    )
    assert classify_beat_override(
        _beat("Conversion fell from 29% down to 12% year-over-year."), settings
    ) == (True, "animated_chart")
    assert classify_beat_override(
        _beat("Compare junior prompt habits versus staff prompt engineering."), settings
    ) == (True, "split_comparison")
    assert classify_beat_override(
        _beat("The debrief rubric marks generic answers as an instant downlevel."), settings
    ) == (True, "rubric_checklist")
    assert classify_beat_override(
        _beat("Requests hit the API gateway before ANN retrieval feeds the ranker pipeline."),
        settings,
    ) == (True, "architecture_flow")
    assert classify_beat_override(
        _beat("Look at this async Python function and its unit test guard."), settings
    ) == (True, "code_ide_mockup")
    monkeypatch_s = settings.model_copy(update={"enable_comment_friction_beat": True})
    assert classify_beat_override(
        _beat("Take-homes penalize employed engineers.", cf=True), monkeypatch_s
    ) == (
        True,
        "rubric_checklist",
    )


def test_router_enforces_silent_preview_opening_bans_ratio_and_periodic_anchors(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_PROGRAMMATIC_GRAPHICS", "true")
    monkeypatch.setenv("TARGET_PROGRAMMATIC_RATIO", "0.30")
    monkeypatch.setenv("MIN_PROGRAMMATIC_RATIO", "0.20")
    monkeypatch.setenv("MAX_PROGRAMMATIC_RATIO", "0.45")
    monkeypatch.setenv("PROGRAMMATIC_ANCHOR_MIN_SEC", "45.0")
    monkeypatch.setenv("PROGRAMMATIC_ANCHOR_MAX_SEC", "60.0")
    reset_settings_cache()
    s = get_settings()

    # Build an 8-minute (480s) 80-beat timeline (6s/beat) where scenes after 0:30 have zero stats,
    # proving periodic anchors prevent the 0:31 quality cliff!
    beats: list[PlannedBeat] = []
    for i in range(80):
        t0, t1 = i * 6.0, (i + 1) * 6.0
        spoken = (
            "Postings dropped 24% across entry roles."
            if i == 0
            else "An engineer walks down a quiet glass hallway toward an interview room."
        )
        beats.append(
            PlannedBeat(
                global_index=i,
                scene_index=i // 8,
                shot_index=i % 8,
                audio_start=t0,
                audio_end=t1,
                audio_duration=6.0,
                viewer_start=t0,
                viewer_end=t1,
                viewer_duration=6.0,
                spoken_text=spoken,
                beat_query="engineer walking glass corridor"
                if i % 2 == 0
                else "modern office laptop",
            )
        )

    def _fake_broll(b: PlannedBeat, *, ultra_strict: bool = False):
        if "corridor" in b.beat_query:
            return ("https://cdn.pexels.com/corridor.mp4", 0.91)
        return (None, 0.0)

    routed = route_visual_storyboard(
        beats, s, broll_Lookup_fn=_fake_broll, video_title="Hiring Bar"
    )

    # 1. Beat 0 opens on high-contrast programmatic hook graphic.
    assert routed[0].visual_type == "programmatic_graphic"
    assert routed[0].template_id in ("big_stat_card", "evidence_card", "split_comparison")

    # 2. Zero ai_image (Ken Burns) and zero generic B-roll inside 0:00-0:30.
    opening = [b for b in routed if b.viewer_start < 30.0]
    assert all(b.visual_type != "ai_image" for b in opening)
    assert all(
        not (b.visual_type == "broll" and "modern office laptop" in b.beat_query) for b in opening
    )

    # 3. Ratio stays strictly within [0.20, 0.45].
    prog_share = sum(1 for b in routed if b.visual_type == "programmatic_graphic") / len(routed)
    assert 0.20 <= prog_share <= 0.45

    # 4. Periodic anchors: max gap without a programmatic graphic never exceeds 60s across 480s.
    last_end = 0.0
    for b in routed:
        if b.visual_type == "programmatic_graphic":
            assert b.viewer_start - last_end <= 60.0
            last_end = b.viewer_end
    assert routed[-1].viewer_end - last_end <= 60.0

    # 5. AI-image beats strictly rotate shot_type.
    ai_shots = [b.shot_type for b in routed if b.visual_type == "ai_image"]
    assert ai_shots == [SHOT_TYPE_CYCLE[i % 3] for i in range(len(ai_shots))]


# --------------------------------------------------------- Workstream 3: B-Roll Gate & Dual-Channel Prompts
@respx.mock
def test_strict_broll_relevance_threshold_drops_borderline_clip() -> None:
    vocab = {"interview", "whiteboard", "architecture", "engineer", "system", "ranking"}
    strong = score_broll_match(
        "engineer whiteboard architecture",
        "engineer, whiteboard, architecture, diagram",
        vocab,
        moment_terms("The engineer sketches the ranking architecture on the whiteboard."),
    )
    weak = score_broll_match(
        "engineer whiteboard architecture",
        "engineer, whiteboard, coffee",
        vocab,
        moment_terms("Latency spikes when the cache misses."),  # zero moment overlap -> 0.0
    )
    assert strong >= 0.85
    assert weak == 0.0

    respx.get(url__startswith="https://pixabay.com/api/videos/").mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "videos": {"large": {"url": "https://cdn.pixabay.com/exact.mp4"}},
                        "tags": "whiteboard, architecture, engineer, system",
                    },
                    {
                        "videos": {"large": {"url": "https://cdn.pixabay.com/partial.mp4"}},
                        "tags": "whiteboard, engineer",
                    },
                ]
            },
        )
    )
    strict_client = PixabayBrollClient("key", min_score=0.92)
    urls = strict_client.search(
        "engineer whiteboard architecture",
        context=" ".join(vocab),
        moment="The engineer sketches system architecture on a whiteboard.",
    )
    assert urls == ["https://cdn.pixabay.com/exact.mp4"]


def test_dual_channel_window_visual_history_cliche_check_and_fakellm_safety(
    monkeypatch, fakes
) -> None:
    # Neither new prompt may contain the word 'judge' (FakeLLM routing hazard).
    for pname in ("scene_image_director_dual.system", "storyboard_critic.system"):
        assert "judge" not in load_prompt(pname).lower()

    lines = ["Beat zero.", "Beat one.", "Target beat two.", "Beat three.", "Beat four."]
    win = build_bidirectional_window(lines, 2, before=2, after=2)
    assert "<prev2_context>Beat zero.</prev2_context>" in win
    assert "<prev1_context>Beat one.</prev1_context>" in win
    assert "<current_context>Target beat two.</current_context>" in win
    assert "<post1_context>Beat three.</post1_context>" in win
    assert "<post2_context>Beat four.</post2_context>" in win

    hist = [
        {
            "shot_type": "wide_environment",
            "setting": "glass atrium",
            "subject": "empty table",
            "dominant_objects": ["glass wall"],
        }
    ]
    h_txt = format_visual_history(hist, window_size=3)
    assert "glass atrium" in h_txt and "wide_environment" in h_txt

    banlist = get_settings().cliche_banlist_list
    hits = find_cliche_hits(
        "A hooded hacker staring at glowing neon code and tangled cables", banlist
    )
    assert {"hooded hacker", "glowing neon code", "tangled cables"} <= set(hits)
    scrubbed = scrub_cliche_phrases(
        "A rack with tangled cables and circuit boards", banlist, style_bible="zero neon glow"
    )
    assert find_cliche_hits(scrubbed, banlist) == []

    llm = fakes.LLM(
        script_json={
            "prompt": "Tight 90mm macro of a milled steel drive latch in cool daylight, zero neon glow, zero floating UI text.",
            "subject": "milled steel drive latch",
            "setting": "server aisle bay",
            "shot_type": "tight_physical_detail",
            "dominant_objects": ["drive latch"],
            "redirect_to_graphic": False,
            "suggested_template": "",
        }
    )
    res = SceneImageDirector(get_settings(), llm).compose_dual(
        all_lines=lines,
        target_index=2,
        required_shot_type="tight_physical_detail",
        visual_history=hist,
        title="Latency Deep Dive",
    )
    assert res is not None
    assert res.visual_meta["shot_type"] == "tight_physical_detail"
    sent_sys = llm.calls[-1]["system"]
    assert "<current_context>Target beat two.</current_context>" in sent_sys
    assert "glass atrium" in sent_sys


# --------------------------------------------------------- Workstream 4: Script & Studio Audio
def test_spoken_citation_offloading_and_comment_friction_generation(monkeypatch, fakes) -> None:
    cleaned, src = _offload_spoken_citation(
        "According to the October 2026 National Student Clearinghouse report, entry postings dropped 31%.",
        None,
    )
    assert cleaned.startswith("Entry postings dropped 31%.")
    assert src == "October 2026 National Student Clearinghouse report"

    monkeypatch.setenv("ENABLE_COMMENT_FRICTION_BEAT", "true")
    monkeypatch.setenv("COMMENT_FRICTION_POSITION_RATIO", "0.60")
    reset_settings_cache()
    s = get_settings()

    brief = DataBrief(
        run_id="R1",
        niche="tech careers",
        key_facts=[
            KeyFact(
                statement="Postings fell 31%",
                metric="postings",
                value="-31%",
                citation=Citation(source="adzuna", snippet="fell 31%", observed_at=utcnow()),
            ),
            KeyFact(
                statement="Median pay is $140,000",
                metric="pay",
                value="$140,000",
                citation=Citation(source="bls", snippet="median $140k", observed_at=utcnow()),
            ),
        ],
        coverage={"adzuna": True},
        provenance=Provenance(produced_by="test"),
    )
    payload = {
        "title_options": ["Why Entry Roles Shifted"],
        "hook": "Entry postings dropped 31% this year.",
        "scenes": [
            {
                "index": i,
                "narration": f"Scene {i} distinct claim about architecture step {i} and postings 31%."
                if i == 0
                else f"Distinct point number {i} explaining system trade-offs clearly.",
                "on_screen_text": f"POINT {i}",
                "b_roll_keywords": ["whiteboard"],
                "fact_ref": 0 if i == 0 else None,
            }
            for i in range(6)
        ],
        "cta": "Like and subscribe.",
        "description": "Test desc.",
        "tags": ["tech"],
        "thumbnail_concept": "Whiteboard",
        "thumbnail_text": "31% DROP",
    }
    script = ScriptGenerator(s, fakes.LLM(script_json=payload)).run(
        "R1", brief, get_template("contrarian")
    )
    ok, note = comment_friction_report(script, target_ratio=0.60)
    assert ok is True, note
    cf_scenes = [sc for sc in script.scenes if sc.is_comment_friction]
    assert len(cf_scenes) == 1
    assert cf_scenes[0].index == 3  # round(5 * 0.60) == 3
    assert len((cf_scenes[0].discussion_question or "").split()) <= 6
    assert script.scenes[0].visual_source == "Adzuna"
    assert average_sentence_words(script) > 0


def test_procedural_sfx_and_ambient_bed_mix_cleanly(tmp_path: Path) -> None:
    import numpy as np

    from content_foundry.production.sound_design import _write_pcm16_wav

    # Synthesize a real 3.0s spoken-level test waveform (-20 dBFS).
    sr = 22050
    t = np.linspace(0.0, 3.0, sr * 3, endpoint=False)
    voice = 0.12 * np.sin(2.0 * np.pi * 220.0 * t)
    narr_wav = _write_pcm16_wav(tmp_path / "narr.wav", voice, sample_rate=sr)

    tick = ensure_transition_sfx_wav(tmp_path, "ui_tick")
    whoosh = ensure_transition_sfx_wav(tmp_path, "soft_whoosh")
    amb = ensure_dark_ambient_wav(tmp_path / "amb.wav", duration_sec=3.0)
    assert tick.is_file() and whoosh.is_file() and amb.is_file()

    mixed_mp3 = tmp_path / "mixed.mp3"
    wrote = mix_studio_audio(
        narr_wav,
        mixed_mp3,
        programmatic_sfx_cues=[(0.0, "soft_whoosh"), (1.4, "ui_tick")],
        programmatic_sfx_gain_db=-14.0,
        ambient_enabled=True,
        ambient_gain_db=-30.0,
        work_dir=tmp_path / "work",
    )
    assert wrote is True
    assert mixed_mp3.is_file() and mixed_mp3.stat().st_size > 2000


def test_timeline_marks_programmatic_graphics_and_renderer_suppresses_duplicate_citation(
    tmp_path: Path, settings, fakes
) -> None:
    vo = VoiceoverAsset(
        run_id="R",
        audio_path="assets/narration.mp3",
        duration_sec=8.0,
        sample_rate=22050,
        voice_id="v",
        provider="fake",
        word_timings=[
            WordTiming(word="Dropped", start=0.5, end=1.0),
            WordTiming(word="31%", start=1.0, end=2.0),
            WordTiming(word="later", start=4.5, end=5.5),
            WordTiming(word="stat", start=5.5, end=7.5),
        ],
        scene_timings=[
            SceneTiming(scene_index=0, start=0.0, end=4.0),
            SceneTiming(scene_index=1, start=4.0, end=8.0),
        ],
        provenance=Provenance(produced_by="test"),
    )
    vp = VisualPackage(
        run_id="R",
        thumbnail_path="assets/thumbnail.png",
        thumbnail_text="HOOK",
        captions_path="assets/captions.srt",
        visual_style="clean",
        scenes=[
            SceneVisual(
                scene_index=0,
                kind="broll",
                path="assets/scenes/scene_0_shot_0.mp4",
                source="programmatic_graphic",
                prompt_or_query="stat",
                on_screen_text="31% DROP · Source: adzuna.com",
                duration_sec=4.0,
                shots=[
                    VisualShot(
                        path="assets/scenes/scene_0_shot_0.mp4",
                        duration_sec=4.0,
                        source="programmatic_graphic",
                        query="stat",
                        visual_type="programmatic_graphic",
                        template_id="big_stat_card",
                        template_props={"value": "-31%", "label": "Drop", "source": "Adzuna"},
                    )
                ],
            ),
            SceneVisual(
                scene_index=1,
                kind="broll",
                path="assets/scenes/scene_1_shot_0.png",
                source="card",
                prompt_or_query="wide architectural room",
                on_screen_text="PAY · Source: bls.gov",
                duration_sec=4.0,
                shots=[
                    VisualShot(
                        path="assets/scenes/scene_1_shot_0.png",
                        duration_sec=4.0,
                        source="card",
                        query="room",
                        prompt="Wide 28mm architectural shot",
                        visual_type="ai_image",
                    )
                ],
            ),
        ],
        provenance=Provenance(produced_by="test"),
    )
    segs = build_timeline(vo, vp, motion_enabled=True)
    assert segs[0].is_graphic == (True,)
    assert segs[0].motions == ("none",)
    assert segs[1].is_graphic == (False,)
    assert segs[1].motions[0] != "none"

    backend = fakes.Render()
    Renderer(settings, backend).run("R", vo, vp, run_root=tmp_path)
    cit_srt = (tmp_path / "assets" / "citations.srt").read_text(encoding="utf-8")
    # Scene 0 already displays its source inside big_stat_card -> suppressed; Scene 1 keeps bls.
    assert "adzuna" not in cit_srt.lower()
    assert "bls" in cit_srt.lower()


# --------------------------------------------------------- Workstream 5: Storyboard Critic
def test_storyboard_critic_selectively_regenerates_only_failing_ai_shots(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_PROGRAMMATIC_GRAPHICS", "true")
    monkeypatch.setenv("ENABLE_STORYBOARD_CRITIC", "true")
    monkeypatch.setenv("CRITIC_MAX_RETRIES", "2")
    reset_settings_cache()
    s = get_settings()

    beats = [
        PlannedBeat(
            global_index=0,
            scene_index=0,
            shot_index=0,
            audio_start=0.0,
            audio_end=15.0,
            audio_duration=15.0,
            viewer_start=0.0,
            viewer_end=15.0,
            viewer_duration=15.0,
            spoken_text="Postings fell 31%.",
            beat_query="stat",
            visual_type="programmatic_graphic",
            template_id="big_stat_card",
            template_props={"value": "-31%", "label": "Drop"},
        ),
        PlannedBeat(
            global_index=1,
            scene_index=0,
            shot_index=1,
            audio_start=15.0,
            audio_end=32.0,
            audio_duration=17.0,
            viewer_start=15.0,
            viewer_end=32.0,
            viewer_duration=17.0,
            spoken_text="Reviewers debrief in the room.",
            beat_query="debrief",
            broll_url="https://cdn.pexels.com/debrief.mp4",
            broll_score=0.90,
            visual_type="broll",
        ),
        PlannedBeat(
            global_index=2,
            scene_index=1,
            shot_index=0,
            audio_start=32.0,
            audio_end=46.0,
            audio_duration=14.0,
            viewer_start=32.0,
            viewer_end=46.0,
            viewer_duration=14.0,
            spoken_text="Clean passing physical scene.",
            beat_query="desk",
            visual_type="ai_image",
            shot_type="wide_environment",
            prompt="Sharp 35mm wide architectural room with concrete table, zero neon glow.",
            visual_meta={
                "subject": "concrete briefing table",
                "setting": "daylit concrete room",
                "shot_type": "wide_environment",
                "dominant_objects": ["table"],
            },
        ),
        PlannedBeat(
            global_index=3,
            scene_index=1,
            shot_index=1,
            audio_start=46.0,
            audio_end=60.0,
            audio_duration=14.0,
            viewer_start=46.0,
            viewer_end=60.0,
            viewer_duration=14.0,
            spoken_text="Failing cliché scene.",
            beat_query="code",
            visual_type="ai_image",
            shot_type="medium_human_action",
            prompt="A hooded hacker surrounded by glowing neon code and matrix rain.",
            visual_meta={
                "subject": "hooded hacker",
                "setting": "daylit concrete room",  # ALSO repeats beat 2's setting!
                "shot_type": "medium_human_action",
                "dominant_objects": ["hoodie"],
            },
        ),
    ]

    t_issues, s_issues = validate_storyboard_deterministic(beats, s)
    assert t_issues == []
    assert 2 not in s_issues  # passing shot untouched
    assert 3 in s_issues  # cliché + repeated setting caught

    regenerated_indices: list[int] = []

    def _regen(b: PlannedBeat, reason: str, prior: list[dict]) -> DualPromptResult:
        regenerated_indices.append(b.global_index)
        assert "cliche_banlist" in reason
        return DualPromptResult(
            prompt="Medium 50mm workspace frame of an anodized test bench with digital probe, zero neon glow, zero floating UI text.",
            visual_meta={
                "subject": "anodized test bench with probe",
                "setting": "hardware verification lab",
                "shot_type": b.shot_type,
                "dominant_objects": ["test bench", "probe"],
            },
        )

    report = StoryboardCritic(s, llm_provider=None).audit_and_repair(
        beats, regenerate_shot_fn=_regen, video_title="Test"
    )
    assert report.ok is True
    assert regenerated_indices == [3]  # ONLY failing beat 3 was regenerated; beat 2 untouched!
    assert "hooded hacker" not in beats[3].prompt.lower()


@respx.mock
def test_pollinations_fast_fails_and_latches_on_402_without_duplicate_fallback(monkeypatch) -> None:
    from content_foundry.providers import build_image_provider
    from content_foundry.providers.image import (
        FallbackImageProvider,
        PollinationsImage,
        _ImageClientError,
    )

    monkeypatch.setenv("IMAGE_PROVIDER", "pollinations")
    monkeypatch.setenv("IMAGE_FALLBACK_PROVIDER", "pollinations")
    reset_settings_cache()
    prov = build_image_provider(get_settings())
    assert not isinstance(prov, FallbackImageProvider)

    route = respx.get(url__startswith="https://image.pollinations.ai/prompt/").mock(
        return_value=httpx.Response(402, text="Payment Required")
    )
    pol = PollinationsImage()
    for _ in range(4):
        with pytest.raises(_ImageClientError):
            pol.generate("modern glass hallway", size="1920x1080")
    # 2 single-shot attempts trip the paywall latch; calls 3 and 4 make ZERO HTTP requests.
    assert route.call_count == 2


def test_motion_graphics_director_authors_complete_props_and_is_fakellm_safe(fakes) -> None:
    from content_foundry.production.visual_router import author_motion_graphics_props

    assert "judge" not in load_prompt("motion_graphics_director.system").lower()
    beat = PlannedBeat(
        global_index=4,
        scene_index=1,
        shot_index=1,
        audio_start=20.0,
        audio_end=26.0,
        audio_duration=6.0,
        viewer_start=20.0,
        viewer_end=26.0,
        viewer_duration=6.0,
        spoken_text="walk away quietly instead of executing standard layoffs",
        scene_narration="Hyperscalers offer voluntary severance so engineers walk away quietly instead of executing standard layoffs that trigger WARN notices.",
        beat_query="office desk",
        visual_source="SEC Filings",
        visual_type="programmatic_graphic",
        template_id="split_comparison",
        template_props={},
    )
    llm = fakes.LLM(
        script_json={
            "graphics": [
                {
                    "global_index": 4,
                    "template_id": "split_comparison",
                    "props": {
                        "title": "LAYOFFS VS VOLUNTARY EXIT",
                        "left_header": "MASS LAYOFFS",
                        "right_header": "VOLUNTARY EXIT OFFER",
                        "left_items": ["Triggers WARN notices & works-council blocks"],
                        "right_items": ["Mutual-consent sign-off bypasses statutory freezes"],
                    },
                }
            ]
        }
    )
    count = author_motion_graphics_props([beat], get_settings(), llm, video_title="VEO Game Theory")
    assert count == 1
    assert beat.template_props is not None
    assert beat.template_props["title"] == "LAYOFFS VS VOLUNTARY EXIT"
    assert beat.template_props["source"] == "SEC Filings"


def test_failed_ai_image_promotes_to_broll_or_motion_graphic_never_static_card(
    monkeypatch, tmp_path: Path, good_script
) -> None:
    """Regression for run 0050: when Pollinations returns 402 (or IMAGE_PROVIDER=none), zero shots
    should fall down to static Pillow 'card' slides."""
    import content_foundry.production.motion_graphics as mg_mod
    from content_foundry.agents.visuals import Visuals

    monkeypatch.setenv("ENABLE_PROGRAMMATIC_GRAPHICS", "true")
    monkeypatch.setenv("ENABLE_STORYBOARD_CRITIC", "true")
    reset_settings_cache()
    s = get_settings()

    def _fake_render_template(self, template_id, props, duration_sec, output_path, **kwargs):
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKE_MP4")
        return out

    monkeypatch.setattr(mg_mod.TemplateRegistry, "render_template", _fake_render_template)

    class _PaywalledImage:
        name = "pollinations"

        def generate(self, prompt: str, size: str = "1920x1080") -> bytes:
            raise RuntimeError("402 Payment Required")

    vo = VoiceoverAsset(
        run_id="0050",
        audio_path="assets/narration.mp3",
        duration_sec=36.0,
        sample_rate=22050,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[
            SceneTiming(scene_index=sc.index, start=i * 12.0, end=(i + 1) * 12.0)
            for i, sc in enumerate(good_script.scenes)
        ],
        provenance=Provenance(produced_by="test"),
    )
    pkg = Visuals(s, image_provider=_PaywalledImage(), broll_client=None, llm_provider=None).run(
        "0050", good_script, vo, run_root=tmp_path
    )
    sources = [sh.source for sv in pkg.scenes for sh in sv.shots]
    assert sources
    assert "card" not in sources
    assert all(src == "programmatic_graphic" for src in sources)


def test_redesigned_write_card_separates_source_badge_cleanly(tmp_path: Path) -> None:
    from content_foundry.agents.visuals import _write_card

    out = tmp_path / "card.png"
    _write_card("SINGLE-ENGINEER SQUADS · Source: techcrunch.com", (1280, 720), out)
    assert out.is_file() and out.stat().st_size > 10_000


def test_taper_join_weights_smooths_internal_pause_cuts_without_changing_length(
    tmp_path: Path,
) -> None:
    import numpy as np

    from content_foundry.production.sound_design import _write_pcm16_wav
    from content_foundry.providers.tts import taper_join_weights

    w = taper_join_weights(100, 10, fade_in=True, fade_out=True)
    assert len(w) == 100
    assert w[0] == 0.0 and w[-1] == 0.0
    assert all(val == 1.0 for val in w[10:90])
    # Monotonic rise on fade-in and monotonic fall on fade-out -> zero clicks at cut joins.
    assert w[:10] == sorted(w[:10])
    assert w[90:] == sorted(w[90:], reverse=True)

    # Lossless .wav export in mix_studio_audio eliminates an entire MP3 re-encode pass.
    sr = 22050
    t = np.linspace(0.0, 2.0, sr * 2, endpoint=False)
    narr_wav = _write_pcm16_wav(tmp_path / "voice.wav", 0.12 * np.sin(2 * np.pi * 200 * t), sr)
    out_wav = tmp_path / "mixed.wav"
    assert mix_studio_audio(narr_wav, out_wav, ambient_enabled=True, work_dir=tmp_path / "w")
    assert out_wav.is_file() and out_wav.read_bytes()[:4] == b"RIFF"
