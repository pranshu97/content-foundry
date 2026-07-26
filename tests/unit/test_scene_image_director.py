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
    payload = {
        "shots": [
            {"beat": "handshake across a desk", "prompt": "a warm cinematic close-up of two hands"},
            {"beat": "impostor syndrome at work", "prompt": "a lone analyst looking uncertain"},
        ]
    }
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


def test_scene_image_director_uses_the_video_title_and_description(monkeypatch, fakes):
    """The beat hints are generic stock-search phrases; the video's own title/description are what
    let the model make a shot about THIS video (the lever that lifted the thumbnail director)."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(
        beats=["person working in modern office"],
        narration="A senior IC stacks liquid equity every year.",
        title="ML Career vs AI Startup: The Math",
        description="Why senior ML roles beat startup equity in expected value.",
    )
    system = llm.calls[-1]["system"]
    assert "ML Career vs AI Startup: The Math" in system
    assert "beat startup equity in expected value" in system
    assert "{title}" not in system and "{description}" not in system  # render_prompt is a .replace


def test_scene_image_director_prompt_teaches_by_example_and_keeps_the_guardrails(
    monkeypatch, fakes
):
    """A few-shot exemplar OVERRIDES written rules, so the worked example must itself obey the
    face/hand/text bans — otherwise it silently teaches the model to break them."""
    settings = _settings(monkeypatch)
    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(settings, llm).compose(beats=["hands typing on mechanical keyboard"])
    system = llm.calls[-1]["system"]
    assert "EXAMPLE INPUT" in system and "EXAMPLE OUTPUT" in system
    assert "Do NOT" in system and "reuse its subjects" in system  # example is not a template
    # The example REFRAMES the person/hands beats rather than shooting them head-on.
    assert "No hands in frame" in system and "silhouette" in system.lower()
    # And the constraints themselves survive alongside it.
    for rule in ("NO visible face", "NO close-up of hands", "no readable text"):
        assert rule in system
    # The house style must not be allowed to demand text on a photographic still.
    assert "IGNORE any part of it asking for text" in system
