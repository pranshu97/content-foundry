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
    WordTiming,
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
    assert "Adzuna" in body  # the source name is shown
    assert "Source:" not in body  # ...as the domain/name only — no 'Source:' prefix
    assert "Junior postings" not in body  # only the source line, not the full callout
    assert body.count("-->") == 1  # only the scene that carries on_screen_text
    assert render.last_citations_path == str(srt)


def test_narration_captions_off_by_default_but_citations_still_burn(settings, tmp_path, fakes):
    # Chatterbox/Piper report no real word timings -> even-split drift, so burned narration captions
    # are OFF by default (YouTube auto-CC covers narration). Source citations still burn (not spoken).
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
    assert settings.captions_enabled is False        # default: rely on YouTube's free auto-CC
    assert render.last_burn_captions is False         # narration captions are not burned
    assert render.last_citations_path is not None     # ...but the source-citation strip still burns


def test_citation_shows_domain_only_when_stat_is_spoken(monkeypatch, tmp_path, fakes):
    monkeypatch.setenv("CITATION_SECONDS", "6")
    reset_settings_cache()
    settings = get_settings()
    voiceover = VoiceoverAsset(
        run_id="R", audio_path="assets/narration.mp3", duration_sec=12.0, sample_rate=16000,
        voice_id="v", provider="fake",
        word_timings=[
            WordTiming(word="Junior", start=0.0, end=0.4),
            WordTiming(word="postings", start=0.4, end=0.9),
            WordTiming(word="fell", start=0.9, end=1.3),
            WordTiming(word="31%", start=5.0, end=5.6),  # the number is spoken late in the scene
        ],
        scene_timings=[SceneTiming(scene_index=0, start=0.0, end=12.0)],
        provenance=Provenance(produced_by="voiceover"),
    )
    visuals = VisualPackage(
        run_id="R", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean",
        scenes=[SceneVisual(scene_index=0, kind="image", path="assets/scenes/scene_0.png",
                            source="card", prompt_or_query="p", duration_sec=12.0,
                            on_screen_text="Junior postings -31% · Source: bls.gov")],
        provenance=Provenance(produced_by="visuals"),
    )
    Renderer(settings, fakes.Render()).run("R", voiceover, visuals, run_root=tmp_path)
    body = (tmp_path / "assets" / "citations.srt").read_text(encoding="utf-8")
    assert "bls" in body and "bls.gov" not in body  # domain only, TLD stripped
    assert "00:00:05,000 -->" in body  # appears exactly when '31%' is spoken, not at scene start
    assert "--> 00:00:11,000" in body  # and clears CITATION_SECONDS (6s) later, well before scene end
    reset_settings_cache()


def test_video_speed_passed_and_duration_adjusted(monkeypatch, tmp_path, fakes):
    monkeypatch.setenv("VIDEO_SPEED", "1.5")
    reset_settings_cache()
    render = fakes.Render()
    video = Renderer(get_settings(), render).run("R", _voiceover(), _visuals(), run_root=tmp_path)
    assert render.last_speed == 1.5
    assert video.duration_sec == round(6.0 / 1.5, 3)  # _voiceover() is 6.0s -> 4.0s at 1.5x


def test_sfx_cue_resolved_only_for_scenes_that_carry_one(settings, tmp_path, fakes):
    visuals = VisualPackage(
        run_id="R", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean",
        scenes=[
            SceneVisual(scene_index=0, kind="broll", path="assets/scenes/scene_0.mp4",
                        source="pexels", prompt_or_query="p", duration_sec=3.0, sfx="whoosh"),
            SceneVisual(scene_index=1, kind="broll", path="assets/scenes/scene_1.mp4",
                        source="pexels", prompt_or_query="p", duration_sec=3.0),
        ],
        provenance=Provenance(produced_by="visuals"),
    )
    sfx = fakes.Sfx()  # resolves to None -> no real mixing, just records the request
    Renderer(settings, fakes.Render(), sfx).run("R", _voiceover(), visuals, run_root=tmp_path)
    assert sfx.requested == ["whoosh"]  # scene 1 has no cue, so it is never asked for


def test_disabled_sfx_client_is_never_queried(settings, tmp_path, fakes):
    from content_foundry.providers.sfx import NullSfxClient

    visuals = VisualPackage(
        run_id="R", thumbnail_path="assets/thumbnail.png", thumbnail_text="t",
        captions_path="assets/captions.srt", visual_style="clean",
        scenes=[SceneVisual(scene_index=0, kind="broll", path="assets/scenes/scene_0.mp4",
                            source="pexels", prompt_or_query="p", duration_sec=3.0, sfx="whoosh"),
                SceneVisual(scene_index=1, kind="broll", path="assets/scenes/scene_1.mp4",
                            source="pexels", prompt_or_query="p", duration_sec=3.0)],
        provenance=Provenance(produced_by="visuals"),
    )
    render = fakes.Render()
    # NullSfxClient.enabled is False -> the renderer skips mixing entirely and audio is untouched.
    Renderer(settings, render, NullSfxClient()).run("R", _voiceover(), visuals, run_root=tmp_path)
    assert render.calls == 1


def test_scene_transition_and_warmth_passed_to_backend(monkeypatch, tmp_path, fakes):
    monkeypatch.setenv("SCENE_TRANSITION", "fade")
    monkeypatch.setenv("SCENE_TRANSITION_SEC", "0.6")
    monkeypatch.setenv("COLOR_WARMTH", "0.3")
    reset_settings_cache()
    render = fakes.Render()
    Renderer(get_settings(), render).run("R", _voiceover(), _visuals(), run_root=tmp_path)
    assert render.last_transition == "fade"
    assert render.last_transition_sec == 0.6
    assert render.last_warmth == 0.3
    assert render.last_subscribe is None  # nudge disabled by default


def test_subscribe_nudge_built_at_midpoint(monkeypatch, tmp_path, fakes):
    monkeypatch.setenv("SUBSCRIBE_NUDGE_ENABLED", "true")
    monkeypatch.setenv("SUBSCRIBE_NUDGE_SEC", "4")
    reset_settings_cache()
    render = fakes.Render()
    Renderer(get_settings(), render).run("R", _voiceover(), _visuals(), run_root=tmp_path)
    spec = render.last_subscribe
    assert spec is not None
    assert (tmp_path / "assets" / "subscribe_badge.png").exists()
    assert spec.start == 1.0  # _voiceover() is 6.0s -> midpoint 3.0, 4s badge starts at 1.0


def test_subscribe_bell_rings_when_badge_appears(monkeypatch, tmp_path, fakes):
    monkeypatch.setenv("SUBSCRIBE_NUDGE_ENABLED", "true")
    monkeypatch.setenv("SFX_ENABLED", "true")
    reset_settings_cache()
    sfx = fakes.Sfx()  # records the keywords the renderer asks it to resolve
    Renderer(get_settings(), fakes.Render(), sfx).run(
        "R", _voiceover(), _visuals(), run_root=tmp_path
    )
    assert sfx.requested == ["bell"]  # the badge's midpoint arrival is announced with a bell


def test_subscribe_bell_can_be_silenced(monkeypatch, tmp_path, fakes):
    monkeypatch.setenv("SUBSCRIBE_NUDGE_ENABLED", "true")
    monkeypatch.setenv("SFX_ENABLED", "true")
    monkeypatch.setenv("SUBSCRIBE_BELL_ENABLED", "false")
    reset_settings_cache()
    sfx = fakes.Sfx()
    Renderer(get_settings(), fakes.Render(), sfx).run(
        "R", _voiceover(), _visuals(), run_root=tmp_path
    )
    assert sfx.requested == []  # the badge still shows, but no bell cue is emitted


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
