"""Workstream 6.2 & 6.3 — Real Headless Chromium + FFmpeg template layout/stream verification
and ~65-second multi-scene end-to-end assembly render test ($0 external API cost)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from content_foundry.agents.renderer import Renderer
from content_foundry.agents.visuals import Visuals
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.models import (
    Provenance,
    SceneCue,
    SceneTiming,
    Script,
    VoiceoverAsset,
    WordTiming,
)
from content_foundry.production.motion_graphics import close_motion_browser
from content_foundry.production.sound_design import _write_pcm16_wav

ROOT = Path(__file__).resolve().parents[2]


def _load_verify_script():
    spec = importlib.util.spec_from_file_location(
        "verify_templates", ROOT / "scripts" / "verify_templates.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_templates"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe required")
def test_all_seven_templates_render_zero_overflow_safe_zone_and_valid_mp4_streams(
    tmp_path: Path,
) -> None:
    mod = _load_verify_script()
    results = mod.verify_all_templates(tmp_path / "templates", width=1920, height=1080, fps=30)
    rendered_ids = {r["template_id"] for r in results}
    assert rendered_ids == {
        "big_stat_card",
        "evidence_card",
        "split_comparison",
        "rubric_checklist",
        "animated_chart",
        "architecture_flow",
        "code_ide_mockup",
    }
    for row in results:
        assert row["layout_ok"] is True
        assert row["width"] == 1920 and row["height"] == 1080
        assert row["pix_fmt"] == "yuv420p"
        assert row["r_frame_rate"] == "30/1"
        assert abs(row["duration"] - 2.5) <= 0.08


class _LocalSyntheticImageProvider:
    """Zero-cost local image generator producing crisp 1920x1080 gradient PNGs."""

    name = "local-synthetic"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, prompt: str, *, size: str = "1920x1080") -> bytes:
        from io import BytesIO

        self.calls.append(prompt)
        w, _, h = size.partition("x")
        width, height = int(w or 1920), int(h or 1080)
        shift = (len(self.calls) * 37) % 120
        arr = np.zeros((height, width, 3), dtype=np.uint8)
        arr[:, :, 0] = 18 + shift // 3
        arr[:, :, 1] = 28 + shift // 2
        arr[:, :, 2] = 44 + shift
        buf = BytesIO()
        Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
        return buf.getvalue()


class _LocalSyntheticBrollClient:
    """Zero-cost local B-roll client returning a real 1920x1080 lavfi MP4 clip for specific beats."""

    enabled = True

    def __init__(self, clip_bytes: bytes) -> None:
        self._bytes = clip_bytes

    def search(self, query: str, *, context: str = "", moment: str = "") -> list[str]:
        if "whiteboard" in query or "server" in query:
            return [f"https://pexels.example/{query.replace(' ', '-')}.mp4"]
        return []

    def download(self, url: str) -> bytes:
        return self._bytes


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe required",
)
def test_end_to_end_65s_multi_scene_storyboard_renders_clean_synced_mp4(
    monkeypatch, tmp_path: Path
) -> None:
    """Workstream 6.3: ~65s synthetic multi-scene dry-run combining:
    - 0:00-0:30 programmatic graphics + literal B-roll (zero Ken Burns AI in opening)
    - ~0:42 Comment Friction rubric_checklist (discussion_prompt)
    - ~0:54 periodic anchor graphic
    - B-roll + AI-image Ken Burns/motion fallback scenes
    - Beat-timed voiceover + programmatic entrance SFX (-14 dB) + dark ambient bed (-30 dB)
    - Real FfmpegBackend.render assembly verified via ffprobe.
    """
    monkeypatch.setenv("ENABLE_PROGRAMMATIC_GRAPHICS", "true")
    monkeypatch.setenv("ENABLE_STORYBOARD_CRITIC", "true")
    monkeypatch.setenv("PROGRAMMATIC_SFX_ENABLED", "true")
    monkeypatch.setenv("ENABLE_AMBIENT_MUSIC", "true")
    monkeypatch.setenv("ENABLE_COMMENT_FRICTION_BEAT", "true")
    monkeypatch.setenv("VIDEO_RESOLUTION", "1920x1080")
    monkeypatch.setenv("VIDEO_FPS", "30")
    monkeypatch.setenv("VIDEO_SPEED", "1.0")
    monkeypatch.setenv("CAPTIONS_ENABLED", "false")
    reset_settings_cache()
    s = get_settings()

    run_root = tmp_path / "run_e2e"
    (run_root / "assets" / "scenes").mkdir(parents=True, exist_ok=True)

    # 1. Create a real 5s 1920x1080 stock clip MP4 via ffmpeg lavfi ($0 cost).
    stock_mp4 = tmp_path / "stock_source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30",
            "-t",
            "5.0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(stock_mp4),
        ],
        check=True,
    )
    stock_bytes = stock_mp4.read_bytes()

    # 2. Build a 5-scene, 65.0-second script spanning all visual tiers.
    scenes = [
        SceneCue(
            index=0,
            narration=(
                "Entry postings dropped 31% year over year while senior latency roles grew 14%. "
                "Engineers sketching whiteboard system boundaries clear the loop twice as fast."
            ),
            on_screen_text="ENTRY POSTINGS -31% · Source: cra.org",
            b_roll_keywords=["stat hook", "engineer whiteboard architecture", "comparison"],
            fact_ref=0,
            visual_source="CRA Benchmark Report",
        ),
        SceneCue(
            index=1,
            narration=(
                "Compare generic prompt habits versus staff engineers who bound tail latency first. "
                "Requests enter the API gateway before ANN retrieval feeds the cross-encoder ranker."
            ),
            on_screen_text="BOUND LATENCY FIRST",
            b_roll_keywords=["engineer whiteboard diagram", "server rack aisle", "pipeline flow"],
        ),
        SceneCue(
            index=2,
            narration=(
                "Quiet preparation inside the hardware lab exposes memory contention before launch. "
                "Teams inspect physical thermal traces and calibrate queue depth under load."
            ),
            on_screen_text="HARDWARE LAB TRACE",
            b_roll_keywords=["quiet lab bench", "thermal probe"],
        ),
        SceneCue(
            index=3,
            narration=(
                "Unpaid twelve-hour take-home assignments filter out employed senior engineers "
                "far faster than underqualified applicants."
            ),
            on_screen_text="TAKE-HOME FILTER TRAP",
            b_roll_keywords=["take home code"],
            is_comment_friction=True,
            discussion_question="Fair signal or broken filter?",
        ),
        SceneCue(
            index=4,
            narration=(
                "Look at this Python fallback guard keeping p99 serving under 15ms during spikes. "
                "If this saved you one painful loop, drop a like and subscribe. See you next time."
            ),
            on_screen_text="15MS SLA GUARD",
            b_roll_keywords=["python sla code", "closing desk"],
        ),
    ]
    script = Script(
        run_id="e2e",
        template_id="contrarian",
        title_options=["Why Senior System Design Loops Downlevel Fast Coders"],
        hook="Entry postings dropped 31% year over year.",
        scenes=scenes,
        cta="Like and subscribe.",
        description="System design and latency trade-offs.",
        tags=["system design", "engineering"],
        thumbnail_concept="Dark rubric card",
        thumbnail_text="31% DROP",
        word_count=120,
        provenance=Provenance(produced_by="test"),
    )

    # 3. Synthesize a real 65.0s narration audio track + monotonic word/scene timings.
    total_sec = 65.0
    sr = 22050
    t_arr = np.linspace(0.0, total_sec, int(sr * total_sec), endpoint=False)
    # Modulated speech-like harmonic envelope so loudnorm & relative dBFS behave realistically.
    syllable_env = 0.5 + 0.5 * np.sin(2.0 * np.pi * 3.2 * t_arr)
    speech_sig = 0.14 * syllable_env * np.sin(2.0 * np.pi * 185.0 * t_arr)
    narr_wav = tmp_path / "narr_raw.wav"
    _write_pcm16_wav(narr_wav, speech_sig, sample_rate=sr)
    narr_mp3 = run_root / "assets" / "narration.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(narr_wav),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(narr_mp3),
        ],
        check=True,
    )

    scene_spans = [(0.0, 14.0), (14.0, 30.0), (30.0, 43.0), (43.0, 54.0), (54.0, 65.0)]
    scene_timings = [
        SceneTiming(scene_index=i, start=a, end=b) for i, (a, b) in enumerate(scene_spans)
    ]
    word_timings: list[WordTiming] = []
    for sc, (a, b) in zip(scenes, scene_spans, strict=True):
        words = sc.narration.split()
        step = (b - a) / len(words)
        for idx, w in enumerate(words):
            word_timings.append(
                WordTiming(
                    word=w, start=round(a + idx * step, 3), end=round(a + (idx + 1) * step, 3)
                )
            )

    vo = VoiceoverAsset(
        run_id="e2e",
        audio_path="assets/narration.mp3",
        duration_sec=total_sec,
        sample_rate=sr,
        voice_id="synthetic",
        provider="synthetic",
        word_timings=word_timings,
        scene_timings=scene_timings,
        provenance=Provenance(produced_by="test"),
    )

    # 4. Run real Visuals stage (Playwright templates + B-roll + synthetic AI stills + Critic).
    img_prov = _LocalSyntheticImageProvider()
    broll_prov = _LocalSyntheticBrollClient(stock_bytes)
    try:
        visuals_pkg = Visuals(
            s, image_provider=img_prov, broll_client=broll_prov, llm_provider=None
        ).run("e2e", script, vo, run_root=run_root)
    finally:
        close_motion_browser()

    all_shots = [sh for sv in visuals_pkg.scenes for sh in sv.shots]
    tiers_present = {sh.visual_type for sh in all_shots}
    assert "programmatic_graphic" in tiers_present
    assert ("broll" in tiers_present) or ("ai_image" in tiers_present)
    assert all_shots[0].visual_type == "programmatic_graphic"

    # 5. Run real FfmpegBackend assembly (including transition SFX + -30 dB ambient mix + loudnorm).
    from content_foundry.providers.render_backend import FfmpegBackend

    backend = FfmpegBackend(
        ffmpeg_path=s.ffmpeg_path,
        video_encoder="libx264",
        loudness_lufs=s.audio_loudness_lufs,
    )
    video_asset = Renderer(s, backend).run("e2e", vo, visuals_pkg, run_root=run_root)

    final_mp4 = run_root / video_asset.video_path
    assert final_mp4.is_file() and final_mp4.stat().st_size > 50_000
    assert (run_root / "assets" / "narration_mixed.wav").is_file()

    # 6. Probe final MP4 streams and verify exact resolution, pixel format, FPS, and A/V sync.
    probe_raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,duration:format=duration",
            "-of",
            "json",
            str(final_mp4),
        ],
        text=True,
    )
    probe = json.loads(probe_raw)
    streams = probe.get("streams") or []
    v_stream = next(st for st in streams if st.get("codec_type") == "video")
    a_stream = next(st for st in streams if st.get("codec_type") == "audio")
    fmt_dur = float((probe.get("format") or {}).get("duration") or 0.0)

    assert int(v_stream["width"]) == 1920
    assert int(v_stream["height"]) == 1080
    assert v_stream["pix_fmt"] == "yuv420p"
    assert v_stream["r_frame_rate"] == "30/1"
    assert a_stream["codec_name"] == "aac"
    assert abs(fmt_dur - total_sec) <= 0.35
    if v_stream.get("duration") and a_stream.get("duration"):
        assert abs(float(v_stream["duration"]) - float(a_stream["duration"])) <= 0.30
