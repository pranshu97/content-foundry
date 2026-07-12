"""Unit: captions (.srt) + render timeline (Ch. 11.3, 12.3)."""

from __future__ import annotations

from content_foundry.models import (
    Provenance,
    SceneTiming,
    SceneVisual,
    VisualPackage,
    VisualShot,
    VoiceoverAsset,
    WordTiming,
)
from content_foundry.production.captions import build_srt, citation_label
from content_foundry.production.timeline import build_timeline


def test_build_srt_groups_and_formats():
    words = [WordTiming(word=f"w{i}", start=float(i), end=float(i) + 0.5) for i in range(9)]
    srt = build_srt(words, max_words=7)
    assert "1\n00:00:00,000 --> " in srt
    # 9 words / 7 per cue => 2 cues
    assert "2\n" in srt


def test_citation_label_shows_domain_without_tld_or_prefix():
    assert citation_label("Junior postings -31% · Source: msoe.edu") == "msoe"
    assert citation_label("Median $112k · Source: www.bls.gov") == "bls"
    assert citation_label("Layoffs up · Source: nytimes.com") == "nytimes"
    # A named source (no dotted domain) is shown as-is; a callout with no source yields nothing.
    assert citation_label("Postings fell · Source: Adzuna") == "Adzuna"
    assert citation_label("Bureau data · Source: Bureau of Labor Statistics") == "Bureau of Labor Statistics"
    assert citation_label("No source here") == ""


def test_build_timeline_locks_to_scene_timings():
    vo = VoiceoverAsset(
        run_id="r", audio_path="assets/narration.mp3", duration_sec=6.0, sample_rate=16000,
        voice_id="v", provider="fake",
        word_timings=[], scene_timings=[
            SceneTiming(scene_index=0, start=0.0, end=3.0),
            SceneTiming(scene_index=1, start=3.0, end=6.0),
        ],
        provenance=Provenance(produced_by="voiceover"),
    )
    visuals = VisualPackage(
        run_id="r", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean",
        scenes=[
            SceneVisual(scene_index=0, kind="image", path="assets/scenes/scene_0.png",
                        source="card", prompt_or_query="p", duration_sec=3.0),
            SceneVisual(scene_index=1, kind="broll", path="assets/scenes/scene_1.mp4",
                        source="pexels", prompt_or_query="q", duration_sec=3.0),
        ],
        provenance=Provenance(produced_by="visuals"),
    )
    timeline = build_timeline(vo, visuals)
    assert [s.index for s in timeline] == [0, 1]
    assert timeline[0].duration == 3.0
    assert timeline[1].visual_kind == "broll"


def test_build_timeline_carries_and_rescales_beat_clips():
    vo = VoiceoverAsset(
        run_id="r", audio_path="assets/narration.mp3", duration_sec=6.0, sample_rate=16000,
        voice_id="v", provider="fake", word_timings=[],
        scene_timings=[SceneTiming(scene_index=0, start=0.0, end=6.0)],
        provenance=Provenance(produced_by="voiceover"),
    )
    visuals = VisualPackage(
        run_id="r", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean",
        scenes=[SceneVisual(
            scene_index=0, kind="broll", path="assets/scenes/scene_0_shot_0.mp4", source="pexels",
            prompt_or_query="q", duration_sec=6.0,
            shots=[
                VisualShot(path="assets/scenes/scene_0_shot_0.mp4", duration_sec=1.0,
                           source="pexels", query="a"),
                VisualShot(path="assets/scenes/scene_0_shot_1.mp4", duration_sec=1.0,
                           source="pixabay", query="b"),
            ],
        )],
        provenance=Provenance(produced_by="visuals"),
    )
    seg = build_timeline(vo, visuals)[0]
    assert [p for p, _ in seg.clips] == [
        "assets/scenes/scene_0_shot_0.mp4", "assets/scenes/scene_0_shot_1.mp4",
    ]
    # shots were 1.0 + 1.0; rescaled to fill the 6s scene -> 3.0 each (stays audio-locked)
    assert [round(d, 3) for _, d in seg.clips] == [3.0, 3.0]
