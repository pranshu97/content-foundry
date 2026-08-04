"""Agent 4 (Voiceover) timing backbone: silence reserved for the opening beat and for sound effects
must shift the voice WITH the visuals, never desync them (Ch. 10)."""

from __future__ import annotations

import pytest

from content_foundry.agents import voiceover as voiceover_mod
from content_foundry.agents.voiceover import Voiceover


@pytest.fixture
def decoded_audio(monkeypatch):
    """Stand in for pydub+ffmpeg: report a fixed 2.0 s per scene so the real (non-fallback) timing
    path runs. Returns the gaps the agent asked to be inserted into the audio."""
    seen: dict[str, list[float]] = {}

    def _fake(chunks, *, gaps=None):
        seen["gaps"] = list(gaps or [])
        return [2.0] * len(chunks), b"audio"

    monkeypatch.setattr(voiceover_mod, "_decode_concat", _fake)
    return seen


def _run(settings, script, tmp_path, fakes):
    return Voiceover(settings, fakes.TTS()).run("R", script, run_root=tmp_path)


def test_opening_lead_in_delays_the_voice_and_scene_one_covers_it(
    settings, good_script, tmp_path, fakes, decoded_audio
):
    for scene in good_script.scenes:
        scene.sfx = None
    asset = _run(settings, good_script, tmp_path, fakes)

    lead_in = settings.voiceover_lead_in_sec
    assert lead_in > 0
    # The silence is really inserted into the audio, only before the first scene.
    assert decoded_audio["gaps"][0] == pytest.approx(lead_in)
    assert decoded_audio["gaps"][1:] == [0.0] * (len(good_script.scenes) - 1)
    # Scene 0 still starts at 0 so its visual holds over the silence...
    assert asset.scene_timings[0].start == 0.0
    # ...but the first word lands AFTER it, so nothing is spoken at sample zero.
    assert asset.word_timings[0].start == pytest.approx(lead_in)
    # Total length matches the padded audio: lead-in + every scene.
    assert asset.duration_sec == pytest.approx(lead_in + 2.0 * len(good_script.scenes))


def test_scene_with_an_effect_gets_silence_so_it_never_plays_over_the_voice(
    settings, good_script, tmp_path, fakes, decoded_audio
):
    for scene in good_script.scenes:
        scene.sfx = None
    good_script.scenes[1].sfx = "whoosh"
    asset = _run(settings, good_script, tmp_path, fakes)

    gap = settings.sfx_gap_sec
    assert gap > 0
    assert decoded_audio["gaps"][1] == pytest.approx(gap)
    cued = asset.scene_timings[1]
    # The effect is cued at the scene start; the first word of that scene comes a full gap later,
    # so the effect plays in the clear instead of on top of the narration.
    first_word = next(w for w in asset.word_timings if w.start >= cued.start)
    assert first_word.start - cued.start == pytest.approx(gap, abs=1e-6)


def test_first_scene_effect_does_not_stack_gap_on_top_of_lead_in(
    settings, good_script, tmp_path, fakes, decoded_audio
):
    for scene in good_script.scenes:
        scene.sfx = None
    good_script.scenes[0].sfx = "whoosh"
    _run(settings, good_script, tmp_path, fakes)

    # A scene needing both takes the LONGER beat, not the sum — no dead air at the top.
    assert decoded_audio["gaps"][0] == pytest.approx(
        max(settings.voiceover_lead_in_sec, settings.sfx_gap_sec)
    )


def test_scene_timings_stay_contiguous_with_gaps(
    settings, good_script, tmp_path, fakes, decoded_audio
):
    good_script.scenes[1].sfx = "whoosh"
    asset = _run(settings, good_script, tmp_path, fakes)

    # No holes or overlaps: every scene begins exactly where the previous one ended, so the render
    # timeline still covers the whole track.
    for previous, following in zip(asset.scene_timings, asset.scene_timings[1:], strict=False):
        assert following.start == pytest.approx(previous.end)
    assert asset.scene_timings[-1].end == pytest.approx(asset.duration_sec)


def test_fallback_path_keeps_timings_unpadded(settings, good_script, tmp_path, fakes):
    # FakeTTS emits undecodable audio, so the agent falls back to estimates and CANNOT pad the track.
    # It must not shift the timings either, or every caption would slide off the voice.
    good_script.scenes[0].sfx = "whoosh"
    asset = _run(settings, good_script, tmp_path, fakes)
    assert asset.scene_timings[0].start == 0.0
    assert asset.word_timings[0].start == 0.0
