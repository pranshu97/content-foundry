"""Unit: deterministic diagram rendering (Future Plans — draw the shots a camera cannot take)."""

from __future__ import annotations

import pytest

from content_foundry.production.diagram import diagram_type, render_diagram

MATRIX = {
    "type": "matrix",
    "title": "The same number is a different job",
    "columns": ["Google", "Meta", "Amazon"],
    "rows": [["Entry", "L3", "E3", "L4"], ["Senior", "L5", "E5", "L6"]],
    "highlight_row": 1,
    "caption": "An Amazon L4 is an entry level engineer.",
}
BARS = {
    "type": "bars",
    "title": "Latency budget",
    "items": [
        {"label": "Retrieval", "value": 3, "note": "3ms"},
        {"label": "Deep ranker", "value": 22, "note": "22ms", "highlight": True},
    ],
}
LADDER = {
    "type": "ladder",
    "title": "What each rung is paid for",
    "steps": [
        {"label": "L3", "detail": "tickets"},
        {"label": "L5", "detail": "ambiguity", "highlight": True},
    ],
}
FLOW = {
    "type": "flow",
    "title": "Two stage ranking",
    "nodes": [
        {"tag": "Stage 1", "name": "Candidate Retrieval", "detail": "millions to hundreds"},
        {"tag": "Stage 2", "name": "Deep Ranker", "detail": "hundreds to ten"},
    ],
}


@pytest.mark.parametrize("spec", [MATRIX, BARS, LADDER, FLOW], ids=lambda s: s["type"])
def test_every_supported_type_renders_a_real_png(spec, tmp_path):
    out = tmp_path / f"{spec['type']}.png"
    assert render_diagram(spec, out, width=960, height=540) is True
    assert out.stat().st_size > 2000  # a blank canvas would be tiny


def test_diagram_type_identifies_only_supported_shapes():
    assert diagram_type(MATRIX) == "matrix"
    assert diagram_type({"type": "MATRIX"}) == "matrix"  # case-insensitive
    for junk in ({}, {"type": "pie"}, None, "matrix", 7, []):
        assert diagram_type(junk) == ""


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "string",
        {},
        {"type": "nope"},
        {"type": "matrix"},
        {"type": "matrix", "columns": []},
        {"type": "bars", "items": []},
        {"type": "flow", "nodes": [1, 2]},
        {"type": "ladder"},
    ],
)
def test_a_bad_spec_returns_false_and_never_raises(bad, tmp_path):
    """The caller still holds a photographic prompt, so a bad spec must DEGRADE to image generation
    rather than take down a visuals stage that is otherwise fine."""
    assert render_diagram(bad, tmp_path / "x.png") is False


def test_long_labels_are_shrunk_to_fit_rather_than_overflowing(tmp_path):
    """This is exactly why graphviz was rejected: with no working text-layout plugin its labels ran
    outside their boxes. matplotlib exposes real metrics, so an over-long label is scaled down."""
    from content_foundry.production.diagram import _fit_fontsize, _new_axes

    fig, ax = _new_axes(960, 540)
    try:
        short = _fit_fontsize(ax, "L4", 0.25, 30.0)
        long = _fit_fontsize(ax, "Candidate Retrieval and Reranking Pipeline", 0.25, 30.0)
        assert short == 30.0  # already fits, left alone
        assert long < short  # too wide, shrunk
        assert long >= 9.0  # but never below the legibility floor
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)


def test_value_and_text_coercion_survives_llm_sloppiness(tmp_path):
    """Specs come from an LLM: numbers arrive as strings, text arrives with stray whitespace."""
    spec = {
        "type": "bars",
        "title": "  spaced   out  ",
        "items": [{"label": "A", "value": "12"}, {"label": "B", "value": None}],
    }
    assert render_diagram(spec, tmp_path / "b.png", width=640, height=360) is True
