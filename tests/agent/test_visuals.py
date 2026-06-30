"""Agent 5 (Visuals) tests: deterministic prompts, cards, captions, B-roll (Ch. 11)."""

from __future__ import annotations

from career_engine.agents import Visuals, build_image_prompt
from career_engine.models import (
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


def test_visuals_use_image_provider(settings, good_script, tmp_path, fakes):
    vo = _voiceover(good_script)
    image = fakes.Image()
    pkg = Visuals(settings, image_provider=image, broll_client=None).run(
        "R", good_script, vo, run_root=tmp_path
    )
    assert image.calls >= 1
    assert any(sv.source == "fake-image" for sv in pkg.scenes)
