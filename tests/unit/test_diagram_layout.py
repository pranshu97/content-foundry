"""Every diagram shape must render, fill the frame and keep its text inside its boxes.

Run 0024 exposed two faults these lock down:
  * a matrix cell whose label was too long ran clean OFF the frame (ink at x=0.999), because
    ``_fit_fontsize`` returns its floor when nothing fits and the caller drew at that size anyway;
  * every renderer anchored its content to a FIXED top, so a two-row matrix and a five-row matrix
    began on the same pixel row and the short one left a dead void beneath it.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from content_foundry.production.diagram import (
    _RUNG_DETAIL_W,
    _RUNG_LABEL_W,
    _RUNG_PAD,
    CONTENT_BOTTOM,
    CONTENT_TOP,
    _split_two,
    render_diagram,
)

BG = np.array([13, 16, 23])


def _ink(path):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    return np.abs(a - BG).sum(axis=2) > 40


def _rows(n):
    return [[f"Row {i}", f"A{i}", f"B{i}"] for i in range(n)]


SHAPES = {
    "matrix": {"type": "matrix", "columns": ["One", "Two"], "rows": [["a", "b"], ["c", "d"]]},
    "bars": {"type": "bars", "items": [{"label": "x", "value": 3}, {"label": "y", "value": 9}]},
    "ladder": {"type": "ladder", "steps": [{"label": "L3"}, {"label": "L4"}, {"label": "L5"}]},
    "flow": {"type": "flow", "nodes": [{"name": "Fetch"}, {"name": "Rank"}, {"name": "Serve"}]},
}


@pytest.mark.parametrize("kind", sorted(SHAPES))
def test_every_shape_renders(tmp_path, kind):
    out = tmp_path / f"{kind}.png"
    assert render_diagram({**SHAPES[kind], "title": kind, "caption": "c"}, out)
    assert out.stat().st_size > 1000


def test_a_long_label_stays_inside_the_frame(tmp_path):
    """The exact run-0024 failure: an over-long matrix cell ran off the right edge."""
    out = tmp_path / "long.png"
    spec = {
        "type": "matrix",
        "title": "Meta System Design Loop",
        "columns": ["Track", "Core Focus", "Target Scale"],
        "rows": [
            ["Full-Stack", "Distributed Caching & DBs", "High QPS Load Balancing"],
            ["ML Systems", "Feature Stores & Inference", "Low Latency / Multi-Billion Params"],
        ],
        "caption": "c",
    }
    assert render_diagram(spec, out)
    cols = np.where(_ink(out).any(axis=0))[0]
    width = _ink(out).shape[1]
    assert cols[0] > 8, "ink touches the left frame edge"
    assert cols[-1] < width - 9, "ink runs off the right frame edge (the run 0024 bug)"


@pytest.mark.parametrize("n", [2, 5])
def test_content_is_centred_in_the_band_not_hugging_a_fixed_top(tmp_path, n):
    """The run-0024 fault was an ASYMMETRIC void: content anchored to a fixed top, dead space below.

    Symmetric breathing room is correct design, so this checks the block is CENTRED rather than
    demanding it bleed to the edges.
    """
    out = tmp_path / f"m{n}.png"
    assert render_diagram(
        {"type": "matrix", "title": "t", "columns": ["A", "B"], "rows": _rows(n), "caption": "c"},
        out,
    )
    ink = _ink(out)
    h = ink.shape[0]
    lo, hi = int((1 - CONTENT_TOP) * h), int((1 - CONTENT_BOTTOM) * h)
    band = ink[lo:hi]
    filled = np.where(band.any(axis=1))[0]
    assert filled.size, "no content drawn inside the band at all"
    top_margin = filled[0] / len(band)
    bottom_margin = (len(band) - 1 - filled[-1]) / len(band)
    assert (
        abs(top_margin - bottom_margin) < 0.12
    ), f"content is not centred: {top_margin:.2f} above vs {bottom_margin:.2f} below"


def test_two_row_and_five_row_matrices_are_not_the_same_picture(tmp_path):
    paths = []
    for n in (2, 5):
        p = tmp_path / f"r{n}.png"
        render_diagram({"type": "matrix", "title": "t", "columns": ["A", "B"], "rows": _rows(n)}, p)
        paths.append(np.asarray(Image.open(p).convert("L").resize((64, 36))).astype(float))
    assert np.abs(paths[0] - paths[1]).mean() > 5.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Low Latency Multi Billion", "Low Latency\nMulti Billion"),
        ("single", "single"),  # nothing to break
        ("", ""),
    ],
)
def test_split_two_breaks_at_the_evenest_word_boundary(text, expected):
    assert _split_two(text) == expected


def test_a_ladder_rung_cannot_have_its_label_and_detail_collide(tmp_path):
    """Run 0024 drew "ML Infra Work" straight through "Search pipelines & feature layers".

    The label is left-aligned and the detail right-aligned inside the SAME box, each fitted
    independently, so nothing but this budget stops them meeting in the middle.
    """
    assert (
        _RUNG_LABEL_W + _RUNG_DETAIL_W + 2 * _RUNG_PAD < 0.92
    ), "ladder label and detail budgets leave no gap between them"
    out = tmp_path / "rung.png"
    assert render_diagram(
        {
            "type": "ladder",
            "title": "Trojan Horse Pivot Pathway",
            "steps": [
                {"label": "ML Infra Work", "detail": "Search pipelines & feature layers"},
                {
                    "label": "MLE Transfer",
                    "detail": "Internal bridge to core AI team",
                    "highlight": True,
                },
            ],
            "caption": "c",
        },
        out,
    )


def test_a_bad_spec_falls_back_instead_of_raising(tmp_path):
    """render_diagram is best-effort: a broken spec must never break a run."""
    assert render_diagram({"type": "matrix"}, tmp_path / "bad.png") is False
    assert render_diagram({"type": "nonsense"}, tmp_path / "bad2.png") is False
    assert render_diagram(None, tmp_path / "bad3.png") is False
