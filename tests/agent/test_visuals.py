"""Agent 5 (Visuals) tests: deterministic prompts, cards, captions, B-roll (Ch. 11)."""

from __future__ import annotations

from content_foundry.agents import Visuals, build_image_prompt
from content_foundry.models import (
    Provenance,
    SceneTiming,
    VoiceoverAsset,
    WordTiming,
)


def _voiceover(script) -> VoiceoverAsset:
    scene_timings = [
        SceneTiming(scene_index=s.index, start=float(s.index * 3), end=float(s.index * 3 + 3))
        for s in script.scenes
    ]
    words = [WordTiming(word=w, start=float(i), end=float(i) + 0.4)
             for i, w in enumerate(script.hook.split())]
    return VoiceoverAsset(
        run_id="R", audio_path="assets/narration.mp3", duration_sec=float(len(script.scenes) * 3),
        sample_rate=16000, voice_id="v", provider="fake",
        word_timings=words, scene_timings=scene_timings, provenance=Provenance(produced_by="voiceover"),
    )


def test_build_image_prompt_is_pure():
    p1 = build_image_prompt(["closed door", "job board"], "BIG TEXT", "clean infographic")
    p2 = build_image_prompt(["closed door", "job board"], "BIG TEXT", "clean infographic")
    assert p1 == p2
    assert "clean infographic" in p1 and "BIG TEXT" in p1 and "closed door, job board" in p1
    assert "no real people" in p1


def test_visuals_render_cards_and_captions(settings, good_script, tmp_path):
    vo = _voiceover(good_script)
    pkg = Visuals(settings, image_provider=None, broll_client=None).run(
        "R", good_script, vo, run_root=tmp_path
    )
    assert (tmp_path / "assets" / "thumbnail.png").exists()
    assert (tmp_path / "assets" / "captions.srt").exists()
    assert pkg.scenes and all(sv.kind == "image" and sv.source == "card" for sv in pkg.scenes)
    for sv in pkg.scenes:
        assert (tmp_path / sv.path).exists()


def test_visuals_use_broll_when_available(settings, good_script, tmp_path, fakes):
    vo = _voiceover(good_script)
    pkg = Visuals(settings, image_provider=None, broll_client=fakes.Broll()).run(
        "R", good_script, vo, run_root=tmp_path
    )
    assert any(sv.kind == "broll" and sv.source == "pexels" for sv in pkg.scenes)


def test_broll_clips_capped_at_two_uses(settings, good_script, tmp_path, fakes):
    # Only 2 clips available for a multi-scene script -> no clip downloaded more than twice.
    from collections import Counter

    broll = fakes.Broll(urls=["https://x/a.mp4", "https://x/b.mp4"])
    Visuals(settings, image_provider=None, broll_client=broll).run(
        "R", good_script, _voiceover(good_script), run_root=tmp_path
    )
    counts = Counter(broll.downloaded)
    assert counts and all(c <= 2 for c in counts.values())


def test_broll_prefers_fresh_clips(settings, good_script, tmp_path, fakes):
    # With a large pool, each scene gets a distinct clip (no repeats).
    broll = fakes.Broll()  # 10 distinct clips
    Visuals(settings, image_provider=None, broll_client=broll).run(
        "R", good_script, _voiceover(good_script), run_root=tmp_path
    )
    assert broll.downloaded and len(set(broll.downloaded)) == len(broll.downloaded)


def test_visuals_use_image_provider(settings, good_script, tmp_path, fakes):
    vo = _voiceover(good_script)
    image = fakes.Image()
    pkg = Visuals(settings, image_provider=image, broll_client=None).run(
        "R", good_script, vo, run_root=tmp_path
    )
    assert image.calls >= 1
    assert any(sv.source == "fake-image" for sv in pkg.scenes)


def test_visuals_split_long_scene_into_ordered_beat_clips(settings, good_script, tmp_path, fakes):
    # A longer scene with several ordered keywords -> one B-roll clip per beat (moment-matched),
    # instead of a single broad clip for the whole scene.
    scene = good_script.scenes[0]
    scene.b_roll_keywords = [
        "handshake across a desk", "reading a job offer letter", "typing on a laptop",
    ]
    one = good_script.model_copy(update={"scenes": [scene]})
    vo = VoiceoverAsset(
        run_id="0001", audio_path="assets/narration.mp3", duration_sec=6.0, sample_rate=16000,
        voice_id="v", provider="fake", word_timings=[],
        scene_timings=[SceneTiming(scene_index=scene.index, start=0.0, end=6.0)],
        provenance=Provenance(produced_by="voiceover"),
    )
    broll = fakes.Broll()  # 10 distinct clips
    pkg = Visuals(settings, image_provider=None, broll_client=broll).run(
        "0001", one, vo, run_root=tmp_path
    )
    sv = pkg.scenes[0]
    assert sv.kind == "broll"
    assert len(sv.shots) == 3  # 6s / 2s-min = 3 beats, and 3 keywords supplied
    assert len(broll.downloaded) == 3  # a distinct clip pulled per beat
    assert [s.query for s in sv.shots] == scene.b_roll_keywords  # each beat -> its own search
    for shot in sv.shots:
        assert (tmp_path / shot.path).exists()
        assert abs(shot.duration_sec - 2.0) < 0.01  # the 6s scene split evenly across 3 beats
