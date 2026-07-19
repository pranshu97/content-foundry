"""Like nudge: glowing badge drawing, early-placement spec, and overlay position expressions."""

from __future__ import annotations

from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.production.like_nudge import (
    LikeSpec,
    build_like_spec,
    render_like_badge,
)


def test_render_badge_no_glow_is_transparent_rgba_of_expected_height(tmp_path):
    from PIL import Image

    out = render_like_badge(tmp_path / "like.png", height_px=80, glow=False)
    assert out.exists()
    img = Image.open(out)
    assert img.mode == "RGBA"
    assert img.height == 80
    assert img.width > img.height  # a wide pill, not a square
    assert img.getpixel((0, 0))[3] == 0  # transparent corner
    assert img.getpixel((img.width // 2, img.height // 2))[3] == 255  # opaque centre


def test_render_badge_with_glow_pads_the_canvas(tmp_path):
    from PIL import Image

    out = render_like_badge(tmp_path / "like_glow.png", height_px=80, glow=True)
    img = Image.open(out)
    assert img.mode == "RGBA"
    assert img.height > 80  # a glow halo adds padding around the pill
    assert img.getpixel((img.width // 2, img.height // 2))[3] == 255  # opaque pill in the centre


def test_build_spec_disabled_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("LIKE_NUDGE_ENABLED", "false")
    reset_settings_cache()
    spec = build_like_spec(
        get_settings(), run_root=tmp_path, total_duration=60.0, frame_height=1080
    )
    assert spec is None


def test_build_spec_places_at_fraction_and_writes_badge(tmp_path, monkeypatch):
    monkeypatch.setenv("LIKE_NUDGE_ENABLED", "true")
    monkeypatch.setenv("LIKE_NUDGE_SEC", "4")
    reset_settings_cache()
    spec = build_like_spec(
        get_settings(), run_root=tmp_path, total_duration=60.0, frame_height=1080
    )
    assert isinstance(spec, LikeSpec)
    assert (tmp_path / "assets" / "like_badge.png").exists()
    assert spec.start == 13.0  # 60 * 0.25 - 4/2
    assert spec.end == 17.0
    assert 0 < spec.fade <= 0.5
    assert spec.pulse > 0.0  # glow on by default => it breathes


def test_build_spec_pulse_is_zero_when_glow_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LIKE_NUDGE_ENABLED", "true")
    monkeypatch.setenv("LIKE_NUDGE_GLOW", "false")
    reset_settings_cache()
    spec = build_like_spec(
        get_settings(), run_root=tmp_path, total_duration=60.0, frame_height=1080
    )
    assert spec is not None
    assert spec.pulse == 0.0


def test_build_spec_skips_when_video_too_short(tmp_path, monkeypatch):
    monkeypatch.setenv("LIKE_NUDGE_ENABLED", "true")
    monkeypatch.setenv("LIKE_NUDGE_SEC", "4")
    reset_settings_cache()
    spec = build_like_spec(
        get_settings(), run_root=tmp_path, total_duration=4.5, frame_height=1080
    )
    assert spec is None  # 4.5 <= 4 + 1


def test_ffmpeg_xy_positions():
    def spec(pos):
        return LikeSpec(
            image_path="b.png", start=1.0, duration=2.0, fade=0.4, position=pos, margin=20
        )

    x, y = spec("bottom-center").ffmpeg_xy()
    assert x == "(main_w-overlay_w)/2"
    assert y == "main_h-overlay_h-20"
    x, y = spec("top-right").ffmpeg_xy()
    assert x == "main_w-overlay_w-20"
    assert y == "20"
    x, y = spec("bottom-left").ffmpeg_xy()
    assert x == "20"
    assert y == "main_h-overlay_h-20"
