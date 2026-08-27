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
    _ladder,
    _matrix,
    _measure,
    _new_axes,
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


# ------------------------------------------------------- run 0025: overlapping column headers
def _header_spans(columns, rows):
    """Rendered [x0, x1] of each column header as a fraction of figure width.

    ``_matrix`` draws the headers first, so the leading texts are the header row.
    """
    import matplotlib.pyplot as plt

    fig, ax = _new_axes(1920, 1080)
    _matrix(ax, {"type": "matrix", "columns": columns, "rows": rows})
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    width = fig.bbox.width
    spans = [
        (t.get_window_extent(renderer).x0 / width, t.get_window_extent(renderer).x1 / width)
        for t in ax.texts[: len(columns)]
    ]
    plt.close(fig)
    return spans


def test_bold_upper_case_is_measured_as_the_wider_string_it_really_is():
    """The root cause of the run-0025 overlap, isolated.

    The caller measured "Safety Guardrail" at regular weight and then drew "SAFETY GUARDRAIL" in
    bold. Both transforms make the string wider, so a size certified as fitting did not fit.
    """
    import matplotlib.pyplot as plt

    fig, ax = _new_axes(1920, 1080)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    plain = _measure(ax, renderer, "Safety Guardrail", 19.0)
    drawn = _measure(ax, renderer, "SAFETY GUARDRAIL", 19.0, "bold")
    plt.close(fig)
    assert drawn > plain * 1.10, (
        "upper-case bold must measure materially wider than mixed-case regular, "
        f"got {plain:.4f} -> {drawn:.4f}"
    )


def test_matrix_column_headers_never_run_into_each_other():
    """The exact run-0025 spec: four columns with long two-word headers.

    Two of the three adjacent pairs used to overlap by ~0.03 of the frame width, which is what made
    "PRIMARY TARGET SAFETY GUARDRAIL PENALTY FUNCTION" read as one unbroken smear.
    """
    spans = _header_spans(
        ["Metric", "Primary Target", "Safety Guardrail", "Penalty Function"],
        [
            ["Ad Revenue", "Maximize eCPM", "Min $0.02/view", "Linear"],
            ["Retention", "Max D30 Active", "Floor 85% D30", "Exponential"],
            ["Latency SLA", "p99 < 40ms", "Hard Cap 50ms", "Step Function"],
        ],
    )
    for (_, left_end), (right_start, _) in zip(spans, spans[1:], strict=False):
        assert right_start > left_end, f"headers overlap by {left_end - right_start:.4f}"


def test_headers_stay_inside_the_frame_even_when_absurdly_long():
    """A header too long to fit at any size must WRAP, never bleed off the edge."""
    spans = _header_spans(
        ["Cross Functional Reliability Guardrail", "Aggregate Downstream Penalty Weighting"],
        [["a", "b"], ["c", "d"]],
    )
    assert spans[0][0] > 0.0, "first header runs off the left edge"
    assert spans[-1][1] < 1.0, "last header runs off the right edge"
    assert spans[1][0] > spans[0][1], "long headers still collide"


def test_a_bar_note_stays_inside_the_frame(tmp_path):
    """Run 0025 pushed "100% Pass" and "3.0x multiplier" clean off the right edge.

    The note is drawn AFTER its bar, so the LONGEST bar dictates how much room is left -- but the
    note was fitted to a flat 0.20 budget that took no account of where the bar ended.
    """
    out = tmp_path / "bars.png"
    assert render_diagram(
        {
            "type": "bars",
            "title": "Engineering Output Leverage",
            "items": [
                {"label": "Manual Code Entry", "value": 1.0, "note": "1.0x baseline"},
                {
                    "label": "AI Architecture",
                    "value": 3.0,
                    "note": "3.0x multiplier",
                    "highlight": True,
                },
            ],
            "caption": "c",
        },
        out,
    )
    ink = _ink(out)
    cols = np.where(ink.any(axis=0))[0]
    assert cols[-1] < ink.shape[1] - 9, "the note on the longest bar runs off the right edge"


def test_a_ladder_label_never_runs_into_its_detail():
    """The exact run-0025 rung: "Market Expansion" was drawn straight through its own detail.

    The width budgets alone cannot prevent this -- both strings are fitted independently and
    ``_fit_fontsize`` draws at its floor even when the floor does not fit, so the left-aligned label
    grew right and the right-aligned detail grew left until they met.
    """
    import matplotlib.pyplot as plt

    fig, ax = _new_axes(1920, 1080)
    _ladder(
        ax,
        {
            "type": "ladder",
            "steps": [
                {"label": "Cost Collapse", "detail": "Automation drops unit build cost"},
                {"label": "Market Expansion", "detail": "New software products become viable"},
                {
                    "label": "Talent Surge",
                    "detail": "Total demand for AI architects multiplies",
                    "highlight": True,
                },
            ],
        },
    )
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    width = fig.bbox.width
    spans = [
        (t.get_window_extent(renderer).x0 / width, t.get_window_extent(renderer).x1 / width)
        for t in ax.texts
    ]
    plt.close(fig)
    # _ladder draws label then detail for each rung, so they pair up in order.
    for (_, label_end), (detail_start, _) in zip(spans[0::2], spans[1::2], strict=False):
        assert detail_start > label_end, f"rung text collides by {label_end - detail_start:.4f}"


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
