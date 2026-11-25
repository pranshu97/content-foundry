"""Agent 4 (Voiceover) timing backbone: silence reserved for the opening beat and for sound effects
must shift the voice WITH the visuals, never desync them (Ch. 10)."""

from __future__ import annotations

import pytest

from content_foundry.agents import voiceover as voiceover_mod
from content_foundry.agents.voiceover import Voiceover
from content_foundry.errors import TTSError


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


def test_scene_audio_preserves_exact_provider_bytes(settings, good_script, tmp_path, fakes):
    import hashlib

    asset = _run(settings, good_script, tmp_path, fakes)
    expected = [fakes.TTS().synthesize(scene.narration) for scene in good_script.scenes]
    for saved, scene, (audio, timings) in zip(
        asset.scene_audio, good_script.scenes, expected, strict=True
    ):
        assert (tmp_path / saved.path).read_bytes() == audio
        assert saved.sha256 == hashlib.sha256(audio).hexdigest()
        assert saved.narration == scene.narration
        assert saved.word_timings == timings
    assert (tmp_path / asset.audio_path).read_bytes() == b"".join(pair[0] for pair in expected)


def test_legacy_voiceover_without_scene_audio_still_loads(settings, good_script, tmp_path, fakes):
    from content_foundry.models import VoiceoverAsset

    payload = _run(settings, good_script, tmp_path, fakes).model_dump()
    payload.pop("scene_audio")
    assert VoiceoverAsset.model_validate(payload).scene_audio == []


def test_selective_revoice_only_synthesizes_selected_scene(
    settings, good_script, tmp_path, fakes, decoded_audio
):
    class RecordingTTS(fakes.TTS):
        texts: list[str] = []

        def synthesize(self, text):
            self.texts.append(text)
            return super().synthesize(text)

    provider = RecordingTTS()
    agent = Voiceover(settings, provider)
    original = agent.run("R", good_script, run_root=tmp_path)
    before = {item.path: (tmp_path / item.path).read_bytes() for item in original.scene_audio}
    selected = good_script.scenes[1]
    selected.narration += " This is the correction."
    provider.texts.clear()
    updated = agent.run(
        "R", good_script, run_root=tmp_path, previous=original, scene_indices={selected.index}
    )
    assert provider.texts == [selected.narration]
    for old, new in zip(original.scene_audio, updated.scene_audio, strict=True):
        assert (tmp_path / old.path).read_bytes() == before[old.path]
        if old.scene_index != selected.index:
            assert old == new
    assert updated.scene_timings == original.scene_timings


@pytest.mark.parametrize(
    "problem", ["missing", "text", "settings", "legacy", "index", "hash", "path"]
)
def test_selective_revoice_refuses_unsafe_reuse_before_synthesis(
    settings, good_script, tmp_path, fakes, decoded_audio, problem
):
    from unittest.mock import patch

    provider = fakes.TTS()
    agent = Voiceover(settings, provider)
    original = agent.run("R", good_script, run_root=tmp_path)
    selected = {good_script.scenes[1].index}
    first = original.scene_audio[0]
    if problem == "missing":
        (tmp_path / first.path).unlink()
    elif problem == "text":
        good_script.scenes[0].narration += " New words."
    elif problem == "settings":
        settings.tts_pause_ratio = 0.8
    elif problem == "legacy":
        original.scene_audio = []
    elif problem == "index":
        selected = {9999}
    elif problem == "hash":
        (tmp_path / first.path).write_bytes(b"edited")
    else:
        first.path = "../outside.mp3"
    before = (tmp_path / original.audio_path).read_bytes()
    with patch.object(provider, "synthesize") as synthesize:
        with pytest.raises(TTSError):
            agent.run(
                "R", good_script, run_root=tmp_path, previous=original, scene_indices=selected
            )
        synthesize.assert_not_called()
    assert (tmp_path / original.audio_path).read_bytes() == before


def test_unchanged_render_does_not_reencode(settings, good_script, tmp_path, fakes, decoded_audio):
    from unittest.mock import patch

    asset = _run(settings, good_script, tmp_path, fakes)
    with patch.object(voiceover_mod, "_decode_concat") as decode:
        assert (
            voiceover_mod.reassemble_saved(settings, good_script, asset, run_root=tmp_path) is asset
        )
        decode.assert_not_called()
    asset.scene_audio = []
    assert voiceover_mod.reassemble_saved(settings, good_script, asset, run_root=tmp_path) is asset


def test_replacement_rebuilds_timing_and_preserves_other_bytes(
    settings, good_script, tmp_path, fakes, decoded_audio, monkeypatch
):
    asset = _run(settings, good_script, tmp_path, fakes)
    before = [(tmp_path / item.path).read_bytes() for item in asset.scene_audio]
    (tmp_path / asset.scene_audio[0].path).write_bytes(b"replacement")

    def decode(chunks, *, gaps=None):
        assert chunks == [b"replacement", *before[1:]]
        return [4.0, *([2.0] * (len(chunks) - 1))], b"assembled"

    monkeypatch.setattr(voiceover_mod, "_decode_concat", decode)
    updated = voiceover_mod.reassemble_saved(settings, good_script, asset, run_root=tmp_path)
    assert updated.duration_sec == pytest.approx(asset.duration_sec + 2.0)
    assert updated.scene_timings[1].start == pytest.approx(asset.scene_timings[1].start + 2.0)
    assert updated.scene_audio[0].word_timings is None
    assert updated.scene_audio[1:] == asset.scene_audio[1:]
    assert updated.provider == asset.provider
    assert updated.voice_id == asset.voice_id
    assert (tmp_path / updated.audio_path).read_bytes() == b"assembled"


def test_bad_replacement_preserves_existing_narration(
    settings, good_script, tmp_path, fakes, decoded_audio, monkeypatch
):
    asset = _run(settings, good_script, tmp_path, fakes)
    before = (tmp_path / asset.audio_path).read_bytes()
    (tmp_path / asset.scene_audio[0].path).write_bytes(b"invalid")
    monkeypatch.setattr(voiceover_mod, "_decode_concat", lambda *args, **kwargs: None)
    with pytest.raises(TTSError, match="could not be decoded"):
        voiceover_mod.reassemble_saved(settings, good_script, asset, run_root=tmp_path)
    assert (tmp_path / asset.audio_path).read_bytes() == before


def test_real_codec_reassembly_is_byte_identical(settings, good_script, tmp_path):
    import shutil
    from io import BytesIO

    from pydub import AudioSegment
    from pydub.generators import Sine

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real codec regression check requires ffmpeg and ffprobe")

    class SyntheticTTS:
        def synthesize(self, text):
            audio = AudioSegment.silent(duration=70) + Sine(440).to_audio_segment(duration=240)
            buffer = BytesIO()
            audio.export(buffer, format="wav")
            return buffer.getvalue(), None

    asset = Voiceover(settings, SyntheticTTS()).run("R", good_script, run_root=tmp_path)
    original = (tmp_path / asset.audio_path).read_bytes()
    (tmp_path / asset.audio_path).unlink()
    rebuilt = voiceover_mod.reassemble_saved(settings, good_script, asset, run_root=tmp_path)
    assert (tmp_path / rebuilt.audio_path).read_bytes() == original
    assert rebuilt.scene_timings == asset.scene_timings
    assert rebuilt.word_timings == asset.word_timings
    assert rebuilt.duration_sec == asset.duration_sec


def test_synthesis_failure_keeps_previous_complete_audio(
    settings, good_script, tmp_path, fakes, decoded_audio
):
    from unittest.mock import patch

    provider = fakes.TTS()
    agent = Voiceover(settings, provider)
    asset = agent.run("R", good_script, run_root=tmp_path)
    before = (tmp_path / asset.audio_path).read_bytes()
    with (
        patch.object(provider, "synthesize", side_effect=TTSError("synthesis failed")),
        pytest.raises(TTSError, match="synthesis failed"),
    ):
        agent.run(
            "R",
            good_script,
            run_root=tmp_path,
            previous=asset,
            scene_indices={good_script.scenes[0].index},
        )
    assert (tmp_path / asset.audio_path).read_bytes() == before
