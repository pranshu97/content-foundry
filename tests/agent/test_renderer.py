"""Agent 6 (Renderer) tests: avatar overlay wiring + has_avatar provenance (future plan 1)."""

from __future__ import annotations

from content_foundry.agents import Renderer
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.models import (
    Provenance,
    SceneTiming,
    SceneVisual,
    VisualPackage,
    VoiceoverAsset,
)


def _voiceover() -> VoiceoverAsset:
    return VoiceoverAsset(
        run_id="R", audio_path="assets/narration.mp3", duration_sec=6.0, sample_rate=16000,
        voice_id="v", provider="fake", word_timings=[],
        scene_timings=[SceneTiming(scene_index=0, start=0.0, end=3.0),
                       SceneTiming(scene_index=1, start=3.0, end=6.0)],
        provenance=Provenance(produced_by="voiceover"),
    )


def _visuals() -> VisualPackage:
    return VisualPackage(
        run_id="R", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean",
        scenes=[SceneVisual(scene_index=i, kind="image", path=f"assets/scenes/scene_{i}.png",
                            source="card", prompt_or_query="p", duration_sec=3.0)
                for i in range(2)],
        provenance=Provenance(produced_by="visuals"),
    )


def test_no_avatar_by_default(settings, tmp_path, fakes):
    render = fakes.Render()
    video = Renderer(settings, render).run("R", _voiceover(), _visuals(), run_root=tmp_path)
    assert video.has_avatar is False
    assert render.last_overlay is None
    assert render.last_citations_path is None  # no on_screen_text -> no citations track


def test_on_screen_citations_burned_as_track(settings, tmp_path, fakes):
    visuals = VisualPackage(
        run_id="R", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean",
        scenes=[
            SceneVisual(scene_index=0, kind="image", path="assets/scenes/scene_0.png",
                        source="card", prompt_or_query="p", duration_sec=3.0,
                        on_screen_text="Junior postings -31% · Source: Adzuna"),
            SceneVisual(scene_index=1, kind="broll", path="assets/scenes/scene_1.mp4",
                        source="pexels", prompt_or_query="p", duration_sec=3.0),
        ],
        provenance=Provenance(produced_by="visuals"),
    )
    render = fakes.Render()
    Renderer(settings, render).run("R", _voiceover(), visuals, run_root=tmp_path)
    srt = tmp_path / "assets" / "citations.srt"
    assert srt.exists()
    body = srt.read_text(encoding="utf-8")
    assert "Source: Adzuna" in body
    assert "Junior postings" not in body  # only the source line, not the full callout
    assert body.count("-->") == 1  # only the scene that carries on_screen_text
    assert render.last_citations_path == str(srt)


def test_avatar_overlay_passed_to_backend(monkeypatch, tmp_path, fakes):
    avatar = tmp_path / "me.png"
    avatar.write_bytes(b"PNG")
    monkeypatch.setenv("AVATAR_OVERLAY_ENABLED", "true")
    monkeypatch.setenv("AVATAR_IMAGE_PATH", str(avatar))
    reset_settings_cache()
    settings = get_settings()

    render = fakes.Render()
    video = Renderer(settings, render).run("R", _voiceover(), _visuals(), run_root=tmp_path)
    assert video.has_avatar is True
    assert render.last_overlay is not None
    assert render.last_overlay.image_path.endswith("me.png")


def test_enabled_but_missing_image_skips_gracefully(monkeypatch, tmp_path, fakes):
    monkeypatch.setenv("AVATAR_OVERLAY_ENABLED", "true")
    monkeypatch.setenv("AVATAR_IMAGE_PATH", str(tmp_path / "does_not_exist.png"))
    reset_settings_cache()
    settings = get_settings()

    render = fakes.Render()
    video = Renderer(settings, render).run("R", _voiceover(), _visuals(), run_root=tmp_path)
    assert video.has_avatar is False
    assert render.last_overlay is None
