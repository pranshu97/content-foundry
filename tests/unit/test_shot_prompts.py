"""Shot prompts are exported so the operator can regenerate any image by hand.

The free image provider is the quality ceiling, not the prompt — so every generated shot's prompt is
written to ``shot_prompts.json`` keyed ``scene_N_shot_M``, ready to paste into a better model.
"""

from __future__ import annotations

import json

from content_foundry.agents.visuals import narration_windows, write_shot_prompts
from content_foundry.models import SceneVisual, VisualShot


def test_narration_windows_splits_a_scene_across_its_shots():
    """Shots divide a scene's duration evenly, so shot j is on screen for the j-th slice of what is
    said — that pairing is what lets an image illustrate the sentence it appears under."""
    narration = " ".join(f"w{i}" for i in range(12))
    windows = narration_windows(narration, 3)
    assert len(windows) == 3
    assert windows[0].startswith("w0") and windows[0].endswith("w3")
    assert windows[2].endswith("w11")
    # Every word is covered exactly once, in order — no gaps, no overlap.
    assert " ".join(windows) == narration


def test_narration_windows_handles_edges():
    # More shots than words: each still gets usable text rather than an empty prompt anchor.
    assert all(w for w in narration_windows("two words here", 5))
    # Empty narration is safe, and the count is always honoured.
    assert narration_windows("", 3) == ["", "", ""]
    assert narration_windows("anything", 0) == []
    # A single shot gets the whole line.
    assert narration_windows("a b c", 1) == ["a b c"]


def _scene(index: int, shots: list[VisualShot]) -> SceneVisual:
    return SceneVisual(
        scene_index=index,
        kind="broll",
        path=shots[0].path,
        source=shots[0].source,
        prompt_or_query="q",
        duration_sec=4.0,
        shots=shots,
    )


def test_write_shot_prompts_keys_every_generated_image(tmp_path):
    scenes = [
        _scene(
            0,
            [
                VisualShot(
                    path="assets/scenes/scene_0_shot_0.mp4",
                    duration_sec=2.0,
                    source="pexels",
                    query="a clip",
                ),  # stock: no prompt
                VisualShot(
                    path="assets/scenes/scene_0_shot_1.png",
                    duration_sec=2.0,
                    source="google",
                    query="a beat",
                    prompt="A tight macro of a server lock.",
                ),
            ],
        ),
        _scene(
            3,
            [
                VisualShot(
                    path="assets/scenes/scene_3_shot_0.png",
                    duration_sec=2.0,
                    source="google",
                    query="another",
                    prompt="An overhead flat-lay of a badge.",
                ),
            ],
        ),
    ]
    written = write_shot_prompts(scenes, tmp_path)
    assert written == 2

    data = json.loads((tmp_path / "shot_prompts.json").read_text(encoding="utf-8"))
    # Keyed so a prompt maps to the exact file the operator would overwrite.
    assert data == {
        "scene_0_shot_1": "A tight macro of a server lock.",
        "scene_3_shot_0": "An overhead flat-lay of a badge.",
    }
    # Stock B-roll carries no prompt, so it must not appear.
    assert "scene_0_shot_0" not in data


def test_write_shot_prompts_skips_a_run_with_no_generated_images(tmp_path):
    scenes = [
        _scene(
            0,
            [
                VisualShot(
                    path="assets/scenes/scene_0_shot_0.mp4",
                    duration_sec=2.0,
                    source="pexels",
                    query="a clip",
                )
            ],
        ),
    ]
    assert write_shot_prompts(scenes, tmp_path) == 0
    assert not (tmp_path / "shot_prompts.json").exists()  # no empty file left behind
