"""Subscribe nudge: badge drawing, midpoint spec, and overlay position expressions (Ch. 12.5)."""

from __future__ import annotations

from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.production.subscribe import (
    SubscribeSpec,
    build_subscribe_spec,
    render_subscribe_badge,
)


def test_render_badge_is_transparent_rgba_of_expected_height(tmp_path):
    from PIL import Image

    out = render_subscribe_badge(tmp_path / "badge.png", height_px=80)
    assert out.exists()
    img = Image.open(out)
    assert img.mode == "RGBA"
    assert img.height == 80
    assert img.width > img.height  # a wide pill, not a square
    # Corners are transparent (rounded pill on a clear canvas); the centre carries opaque pixels.
    assert img.getpixel((0, 0))[3] == 0
    assert img.getpixel((img.width // 2, img.height // 2))[3] == 255


def test_build_spec_disabled_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBSCRIBE_NUDGE_ENABLED", "false")
    reset_settings_cache()
    spec = build_subscribe_spec(
        get_settings(), run_root=tmp_path, total_duration=60.0, frame_height=1080
    )
    assert spec is None


def test_build_spec_centres_on_midpoint_and_writes_badge(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBSCRIBE_NUDGE_ENABLED", "true")
    monkeypatch.setenv("SUBSCRIBE_NUDGE_SEC", "4")
    reset_settings_cache()
    spec = build_subscribe_spec(
        get_settings(), run_root=tmp_path, total_duration=60.0, frame_height=1080
    )
    assert isinstance(spec, SubscribeSpec)
    assert (tmp_path / "assets" / "subscribe_badge.png").exists()
    assert spec.start == 28.0  # 60/2 - 4/2
    assert spec.end == 32.0
    assert 0 < spec.fade <= 0.5


def test_build_spec_skips_when_video_too_short(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBSCRIBE_NUDGE_ENABLED", "true")
    monkeypatch.setenv("SUBSCRIBE_NUDGE_SEC", "4")
    reset_settings_cache()
    spec = build_subscribe_spec(
        get_settings(), run_root=tmp_path, total_duration=4.5, frame_height=1080
    )
    assert spec is None  # 4.5 <= 4 + 1


def test_ffmpeg_xy_positions():
    def spec(pos):
        return SubscribeSpec(
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
