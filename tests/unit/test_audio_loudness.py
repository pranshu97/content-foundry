"""Master loudness: the finished video must arrive at the platform target, not below it."""

from __future__ import annotations

import pytest

from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.providers.render_backend import loudnorm_enabled, loudnorm_opts


def test_loudnorm_targets_the_platform_loudness_with_peak_headroom():
    opts = loudnorm_opts(-14.0)
    # YouTube normalises to ~-14 LUFS and only ever turns audio DOWN, so we must ARRIVE at target.
    assert opts["I"] == -14.0
    # True peak stays below full scale so the lossy AAC encode can't overshoot into clipping.
    assert opts["TP"] < 0.0
    assert opts["LRA"] > 0.0


@pytest.mark.parametrize(
    ("lufs", "on"),
    [(-14.0, True), (-23.0, True), (-0.5, True), (0.0, False), (1.0, False)],
)
def test_loudnorm_is_disabled_only_by_a_non_negative_target(lufs, on):
    """LUFS targets are always negative, so 0 is the natural 'leave it alone' switch."""
    assert loudnorm_enabled(lufs) is on


def test_default_loudness_matches_the_youtube_target(monkeypatch):
    """Raw TTS lands near -31 dBFS; shipping that unnormalised is why narration was inaudible."""
    monkeypatch.delenv("AUDIO_LOUDNESS_LUFS", raising=False)
    reset_settings_cache()
    assert get_settings().audio_loudness_lufs == -14.0
    reset_settings_cache()
