"""Unit: the director may hand a shot a DIAGRAM spec instead of leaving it to a paid image call."""

from __future__ import annotations

from types import SimpleNamespace


def _settings(monkeypatch):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("SCENE_IMAGE_DIRECTOR_ENABLED", "true")
    reset_settings_cache()
    return get_settings()


def test_director_carries_a_diagram_spec_without_changing_its_return_contract(monkeypatch, fakes):
    """compose() must keep returning {shot: prompt} so every existing caller is untouched; the spec
    rides a side channel. The prompt is still REQUIRED, which is what guarantees a fallback."""
    from content_foundry.agents.scene_image_director import SceneImageDirector

    payload = {
        "shots": [
            {
                "shot": 0,
                "prompt": "a photographic fallback",
                "diagram": {
                    "type": "matrix",
                    "columns": ["Google", "Amazon"],
                    "rows": [["Entry", "L3", "L4"]],
                },
            },
            {"shot": 1, "prompt": "just a photo"},
            {"shot": 2, "prompt": "bad spec", "diagram": {"type": "pie"}},
        ]
    }
    director = SceneImageDirector(_settings(monkeypatch), fakes.LLM(script_json=payload))
    prompts = director.compose(shots=[(0, "a"), (1, "b"), (2, "c")])

    assert prompts == {0: "a photographic fallback", 1: "just a photo", 2: "bad spec"}
    assert set(director.diagrams) == {0}  # only the VALID spec is kept
    assert director.diagrams[0]["type"] == "matrix"


def test_prompt_teaches_the_four_shapes_and_keeps_the_photo_fallback(monkeypatch, fakes):
    from content_foundry.agents.scene_image_director import SceneImageDirector

    llm = fakes.LLM(script_json={"shots": []})
    SceneImageDirector(_settings(monkeypatch), llm).compose(shots=[(0, "levels differ by company")])
    system = llm.calls[-1]["system"]
    assert "DRAW IT INSTEAD OF PHOTOGRAPHING IT" in system
    for shape in ("matrix", "bars", "ladder", "flow"):
        assert f'"type": "{shape}"' in system
    # The photographic prompt must survive as the fallback, and diagrams must stay the exception.
    assert 'ALWAYS keep the "prompt" too' in system
    assert "MOST shots to carry no diagram" in system


def test_a_diagram_shot_skips_image_generation_and_a_failure_falls_back(
    monkeypatch, tmp_path, fakes
):
    """The whole point is replacing a PAID image call. A valid spec must render locally; an
    unrenderable one must still produce a shot via the normal image path, never a hole."""
    from content_foundry.agents.visuals import Visuals

    settings = _settings(monkeypatch)

    class _NoBroll:
        enabled = True

        def search(self, query, *, context="", moment=""):
            return []

        def download(self, url):  # pragma: no cover - nothing is returned to download
            raise AssertionError("no clip should be downloaded")

    image = fakes.Image()
    visuals = Visuals(settings, image_provider=image, broll_client=_NoBroll())
    scene = SimpleNamespace(
        index=0,
        narration="Levels differ. And they differ again.",
        b_roll_keywords=["a beat", "another beat"],
        on_screen_text=None,
        cut=None,
    )
    # Shot 0 gets a renderable matrix; shot 1 gets a spec that will fail to render.
    visuals._shot_image_prompts = lambda *a, **k: {0: "photo one", 1: "photo two"}  # noqa: ARG005
    visuals._shot_diagrams = {
        0: {"type": "matrix", "columns": ["Google"], "rows": [["Entry", "L3"]]},
        1: {"type": "matrix", "columns": [], "rows": []},  # invalid -> render_diagram returns False
    }
    shots = visuals._build_shots(scene, tmp_path, duration=20.0, picker=_Picker())

    by_index = dict(enumerate(shots))
    assert by_index[0].source == "diagram"  # drawn locally, no image provider call
    assert by_index[1].source != "diagram"  # unrenderable -> fell back, still produced a shot
    for shot in shots:
        assert (tmp_path / shot.path).exists()


class _Picker:
    def pick(self, candidates):
        return None
