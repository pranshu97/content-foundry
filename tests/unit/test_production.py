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
    assert (
        citation_label("Bureau data · Source: Bureau of Labor Statistics")
        == "Bureau of Labor Statistics"
    )
    assert citation_label("No source here") == ""


def test_build_timeline_locks_to_scene_timings():
    vo = VoiceoverAsset(
        run_id="r",
        audio_path="assets/narration.mp3",
        duration_sec=6.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[
            SceneTiming(scene_index=0, start=0.0, end=3.0),
            SceneTiming(scene_index=1, start=3.0, end=6.0),
        ],
        provenance=Provenance(produced_by="voiceover"),
    )
    visuals = VisualPackage(
        run_id="r",
        thumbnail_path="assets/thumbnail.png",
        thumbnail_text="t",
        captions_path="assets/captions.srt",
        visual_style="clean",
        scenes=[
            SceneVisual(
                scene_index=0,
                kind="image",
                path="assets/scenes/scene_0.png",
                source="card",
                prompt_or_query="p",
                duration_sec=3.0,
            ),
            SceneVisual(
                scene_index=1,
                kind="broll",
                path="assets/scenes/scene_1.mp4",
                source="pexels",
                prompt_or_query="q",
                duration_sec=3.0,
            ),
        ],
        provenance=Provenance(produced_by="visuals"),
    )
    timeline = build_timeline(vo, visuals)
    assert [s.index for s in timeline] == [0, 1]
    assert timeline[0].duration == 3.0
    assert timeline[1].visual_kind == "broll"


def test_build_timeline_carries_and_rescales_beat_clips():
    vo = VoiceoverAsset(
        run_id="r",
        audio_path="assets/narration.mp3",
        duration_sec=6.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[SceneTiming(scene_index=0, start=0.0, end=6.0)],
        provenance=Provenance(produced_by="voiceover"),
    )
    visuals = VisualPackage(
        run_id="r",
        thumbnail_path="assets/thumbnail.png",
        thumbnail_text="t",
        captions_path="assets/captions.srt",
        visual_style="clean",
        scenes=[
            SceneVisual(
                scene_index=0,
                kind="broll",
                path="assets/scenes/scene_0_shot_0.mp4",
                source="pexels",
                prompt_or_query="q",
                duration_sec=6.0,
                shots=[
                    VisualShot(
                        path="assets/scenes/scene_0_shot_0.mp4",
                        duration_sec=1.0,
                        source="pexels",
                        query="a",
                    ),
                    VisualShot(
                        path="assets/scenes/scene_0_shot_1.mp4",
                        duration_sec=1.0,
                        source="pixabay",
                        query="b",
                    ),
                ],
            )
        ],
        provenance=Provenance(produced_by="visuals"),
    )
    seg = build_timeline(vo, visuals)[0]
    assert [p for p, _ in seg.clips] == [
        "assets/scenes/scene_0_shot_0.mp4",
        "assets/scenes/scene_0_shot_1.mp4",
    ]
    # shots were 1.0 + 1.0; rescaled to fill the 6s scene -> 3.0 each (stays audio-locked)
    assert [round(d, 3) for _, d in seg.clips] == [3.0, 3.0]


def test_shots_cut_where_their_words_are_spoken_not_on_an_even_split():
    """A scene's span opens with the silence the voiceover reserves for the sound effect. Splitting
    the span evenly hands a slice of that silence to every shot, so each image lands ahead of the
    words it illustrates — measured at a full second early on the first shot of every scene. Cutting
    on the real word timestamps fixes it; the first shot keeps the silence so a picture is up from
    frame one."""
    # 1s of reserved silence, then 8 evenly spoken words.
    words = [WordTiming(word=f"w{i}", start=1.0 + i * 0.5, end=1.4 + i * 0.5) for i in range(8)]
    vo = VoiceoverAsset(
        run_id="r",
        audio_path="assets/narration.mp3",
        duration_sec=5.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=words,
        scene_timings=[SceneTiming(scene_index=0, start=0.0, end=5.0)],
        provenance=Provenance(produced_by="voiceover"),
    )
    shots = [
        VisualShot(
            path=f"assets/scenes/scene_0_shot_{i}.mp4", duration_sec=1.0, source="s", query="q"
        )
        for i in range(2)
    ]
    visuals = VisualPackage(
        run_id="r",
        thumbnail_path="assets/thumbnail.png",
        thumbnail_text="t",
        captions_path="assets/captions.srt",
        visual_style="clean",
        scenes=[
            SceneVisual(
                scene_index=0,
                kind="broll",
                path=shots[0].path,
                source="s",
                prompt_or_query="q",
                duration_sec=5.0,
                shots=shots,
            )
        ],
        provenance=Provenance(produced_by="visuals"),
    )
    seg = build_timeline(vo, visuals)[0]
    # The second shot illustrates words 4-7, the first of which is spoken at 3.0s.
    assert [round(d, 3) for _, d in seg.clips] == [3.0, 2.0]
    # An even split would have swapped it to the wrong side of the line.
    assert seg.clips[0][1] != seg.duration / 2
    # Whatever the cut, the shots still fill the audio-locked scene exactly.
    assert round(sum(d for _, d in seg.clips), 6) == round(seg.duration, 6)


def test_shot_cuts_fall_back_to_an_even_split_without_usable_word_timings():
    """Not every voice provider returns word timings, and a scene can be pure sound effect. Neither
    may produce a zero-length beat, which would drop the shot from the render entirely."""
    vo = VoiceoverAsset(
        run_id="r",
        audio_path="assets/narration.mp3",
        duration_sec=4.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=[WordTiming(word="only", start=0.5, end=0.9)],
        scene_timings=[SceneTiming(scene_index=0, start=0.0, end=4.0)],
        provenance=Provenance(produced_by="voiceover"),
    )
    shots = [
        VisualShot(
            path=f"assets/scenes/scene_0_shot_{i}.mp4", duration_sec=1.0, source="s", query="q"
        )
        for i in range(4)
    ]
    visuals = VisualPackage(
        run_id="r",
        thumbnail_path="assets/thumbnail.png",
        thumbnail_text="t",
        captions_path="assets/captions.srt",
        visual_style="clean",
        scenes=[
            SceneVisual(
                scene_index=0,
                kind="broll",
                path=shots[0].path,
                source="s",
                prompt_or_query="q",
                duration_sec=4.0,
                shots=shots,
            )
        ],
        provenance=Provenance(produced_by="visuals"),
    )
    seg = build_timeline(vo, visuals)[0]
    assert [round(d, 3) for _, d in seg.clips] == [1.0, 1.0, 1.0, 1.0]


def test_timeline_moves_stills_but_never_touches_real_footage():
    """A still between moving clips reads as a stall, so it gets a slow camera move. Stock footage
    already moves — adding a move there would double up and look wrong."""
    from content_foundry.production.motion import NONE

    vo = VoiceoverAsset(
        run_id="r",
        audio_path="assets/narration.mp3",
        duration_sec=6.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[SceneTiming(scene_index=0, start=0.0, end=6.0)],
        provenance=Provenance(produced_by="voiceover"),
    )
    visuals = VisualPackage(
        run_id="r",
        thumbnail_path="assets/thumbnail.png",
        thumbnail_text="t",
        captions_path="assets/captions.srt",
        visual_style="clean",
        scenes=[
            SceneVisual(
                scene_index=0,
                kind="broll",
                path="assets/scenes/scene_0_shot_0.mp4",
                source="pexels",
                prompt_or_query="q",
                duration_sec=6.0,
                shots=[
                    VisualShot(
                        path="assets/scenes/scene_0_shot_0.mp4",
                        duration_sec=1.0,
                        source="pexels",
                        query="a",
                    ),
                    VisualShot(
                        path="assets/scenes/scene_0_shot_1.png",
                        duration_sec=1.0,
                        source="google",
                        query="b",
                        prompt="An extreme macro shot of a fibre panel, razor-thin depth",
                    ),
                ],
            )
        ],
        provenance=Provenance(produced_by="visuals"),
    )
    seg = build_timeline(vo, visuals)[0]
    assert len(seg.motions) == len(seg.clips)
    assert seg.motions[0] == NONE, "a real clip must never be given a camera move"
    assert seg.motions[1] != NONE, "the still must move so it doesn't freeze between clips"

    # And the whole feature switches off cleanly.
    off = build_timeline(vo, visuals, motion_enabled=False)[0]
    assert set(off.motions) == {NONE}
