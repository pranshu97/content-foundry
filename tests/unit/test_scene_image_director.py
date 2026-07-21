"""Scene Image Director (Agent 5.7): the LLM writes a vivid image prompt for a shot that got no stock
B-roll, so the gap is filled with a bespoke, on-topic image instead of a borrowed off-topic clip."""

from __future__ import annotations

from content_foundry.agents.scene_image_director import SceneImageDirector
from content_foundry.config import get_settings, reset_settings_cache


def _settings(monkeypatch, *, enabled: bool = True):
    monkeypatch.setenv("SCENE_IMAGE_DIRECTOR_ENABLED", "true" if enabled else "false")
    reset_settings_cache()
    return get_settings()


def test_scene_image_director_writes_a_prompt_per_beat(monkeypatch, fakes):
    settings = _settings(monkeypatch)
    payload = {"shots": [
        {"beat": "handshake across a desk", "prompt": "a warm cinematic close-up of two hands"},
        {"beat": "impostor syndrome at work", "prompt": "a lone analyst looking uncertain"},
    ]}
    llm = fakes.LLM(script_json=payload)
    out = SceneImageDirector(settings, llm).compose(
        beats=["handshake across a desk", "impostor syndrome at work"],
        narration="You shake on the offer, then the doubt creeps in.",
    )
    assert out["handshake across a desk"].startswith("a warm cinematic close-up")
    assert out["impostor syndrome at work"].startswith("a lone analyst")
    assert llm.call_count == 1
    # The scene's narration is handed to the model so the generated image stays on-topic + in-world.
    assert "doubt creeps in" in llm.calls[-1]["system"]


def test_scene_image_director_matches_beats_case_insensitively(monkeypatch, fakes):
    settings = _settings(monkeypatch)
    # The model echoes the beat with different casing -> still mapped back to the exact input beat so
    # the caller's `prompts.get(beat)` lookup hits.
    payload = {"shots": [{"beat": "Handshake Across A Desk", "prompt": "vivid handshake scene"}]}
    out = SceneImageDirector(settings, fakes.LLM(script_json=payload)).compose(
        beats=["handshake across a desk"]
    )
    assert out == {"handshake across a desk": "vivid handshake scene"}


def test_scene_image_director_empty_on_no_beats_or_unusable_output(monkeypatch, fakes):
    settings = _settings(monkeypatch)
    assert SceneImageDirector(settings, fakes.LLM()).compose(beats=[]) == {}  # nothing to do
    # A payload without the expected "shots" shape yields nothing usable (caller falls back).
    out = SceneImageDirector(settings, fakes.LLM(script_json={"unexpected": True})).compose(
        beats=["x"]
    )
    assert out == {}
