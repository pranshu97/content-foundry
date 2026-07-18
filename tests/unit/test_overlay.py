"""Unit: avatar overlay spec — deterministic placement + graceful skip (future plan 1)."""

from __future__ import annotations

from types import SimpleNamespace

from content_foundry.production.overlay import OverlaySpec, build_overlay_spec


def _spec(position: str) -> OverlaySpec:
    return OverlaySpec(image_path="/x/avatar.png", position=position, scale=0.2, margin=24)


def test_ffmpeg_xy_for_every_corner():
    assert _spec("top-left").ffmpeg_xy() == ("24", "24")
    assert _spec("top-right").ffmpeg_xy() == ("main_w-overlay_w-24", "24")
    assert _spec("bottom-left").ffmpeg_xy() == ("24", "main_h-overlay_h-24")
    assert _spec("bottom-right").ffmpeg_xy() == (
        "main_w-overlay_w-24",
        "main_h-overlay_h-24",
    )


def test_scaled_height_floors_at_one():
    assert _spec("top-left").scaled_height(1080) == 216
    assert OverlaySpec("/x", "top-left", 0.0001, 0).scaled_height(100) == 1


def _settings(tmp_path, *, enabled=True, name="avatar.png", make=True, position="bottom-right"):
    path = tmp_path / name
    if make:
        path.write_bytes(b"PNG")
    return SimpleNamespace(
        avatar_overlay_enabled=enabled,
        avatar_image_path=str(path),
        avatar_position=position,
        avatar_scale=0.18,
        effective_avatar_scale=0.18,
        effective_avatar_position=position,
        avatar_margin=24,
    )


def test_build_spec_returns_none_when_disabled(tmp_path):
    assert build_overlay_spec(_settings(tmp_path, enabled=False)) is None


def test_build_spec_returns_none_when_image_missing(tmp_path):
    assert build_overlay_spec(_settings(tmp_path, make=False)) is None


def test_build_spec_returns_none_when_path_blank():
    s = SimpleNamespace(avatar_overlay_enabled=True, avatar_image_path="  ",
                        avatar_position="bottom-right", avatar_scale=0.18, avatar_margin=24)
    assert build_overlay_spec(s) is None


def test_build_spec_when_enabled_and_present(tmp_path):
    spec = build_overlay_spec(_settings(tmp_path))
    assert spec is not None
    assert spec.position == "bottom-right"
    assert spec.image_path.endswith("avatar.png")


def test_build_spec_uses_effective_avatar_scale(tmp_path):
    # The renderer overlay must use the format-aware scale (Shorts = 1/2 of long), not avatar_scale.
    s = _settings(tmp_path)
    s.effective_avatar_scale = 0.1
    assert build_overlay_spec(s).scale == 0.1


def test_build_spec_uses_effective_avatar_position(tmp_path):
    # The overlay must use the format-aware corner (Shorts pin TOP-RIGHT), not the raw avatar_position.
    s = _settings(tmp_path, position="bottom-right")
    s.effective_avatar_position = "top-right"
    assert build_overlay_spec(s).position == "top-right"


def test_build_spec_falls_back_on_bad_position(tmp_path):
    spec = build_overlay_spec(_settings(tmp_path, position="middle"))
    assert spec is not None and spec.position == "bottom-right"
