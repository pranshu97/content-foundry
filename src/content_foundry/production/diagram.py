"""Deterministic diagram rendering for shots the camera cannot honestly take (Future Plans).

A large share of this channel's shots are not photographs at all — they are a levelling matrix, a
comp band, a tier ladder, a two-stage pipeline. The image director was faking those photographically
("a printed comparison sheet on a matte desk"), which costs a paid image call AND hands the text to a
model that garbles it. Drawing them for real is free, exact, and reads as a designed asset.

matplotlib only, deliberately. graphviz was evaluated and rejected: on this platform its plugin set
has no working ``textlayout``, so every label overflows its node — fatal when the labels ARE the
content. matplotlib exposes real text metrics, which is what makes ``_fit_fontsize`` below possible.

Pure and best-effort: ``render_diagram`` returns False rather than raising, so a bad spec falls back
to ordinary image generation instead of breaking a run. The output is a plain PNG still, so the
existing camera-motion pass animates it exactly like any other image.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# House palette. Deep near-black so the diagram sits with the graded footage rather than flashing
# white, one cool accent for structure and one warm accent for the thing being pointed at.
BG = "#0d1017"
FG = "#e8eaed"
MUTED = "#8b93a7"
ACCENT = "#7aa2f7"
WARM = "#f0a868"
PANEL = "#1a2030"
PANEL_HOT = "#2a2018"

_TYPES = ("matrix", "bars", "ladder", "flow")
_DPI = 160


def _clean(value: Any) -> str:
    """Spec text comes from an LLM, so coerce and strip rather than trusting the type."""
    return " ".join(str(value or "").split())


def _fit_fontsize(ax, text: str, max_frac: float, start: float, floor: float = 9.0) -> float:
    """Largest font size at which ``text`` fits ``max_frac`` of the axes width.

    This is the whole reason matplotlib won over graphviz: real metrics mean a label can be measured
    and shrunk instead of silently overflowing its box. Shrinks rather than truncates, because a
    clipped word reads as a rendering bug while slightly smaller type just reads as design.
    """
    if not text:
        return start
    fig = ax.figure
    fig.canvas.draw()  # a renderer must exist before anything can be measured
    renderer = fig.canvas.get_renderer()
    size = start
    while size > floor:
        probe = ax.text(0.5, 0.5, text, fontsize=size, ha="center", va="center")
        width = probe.get_window_extent(renderer).width / fig.bbox.width
        probe.remove()
        if width <= max_frac:
            return size
        size -= 1.0
    return floor


def _new_axes(width: int, height: int):
    import matplotlib

    matplotlib.use("Agg")  # headless: a render box has no display
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(width / _DPI, height / _DPI), dpi=_DPI, facecolor=BG)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


def _title(ax, text: str) -> None:
    text = _clean(text).upper()
    if not text:
        return
    size = _fit_fontsize(ax, text, 0.86, 30.0, 16.0)
    ax.text(0.5, 0.90, text, ha="center", va="center", color=FG, fontsize=size, fontweight="bold")


def _caption(ax, text: str) -> None:
    text = _clean(text)
    if not text:
        return
    size = _fit_fontsize(ax, text, 0.84, 20.0, 12.0)
    ax.text(0.5, 0.09, text, ha="center", va="center", color=MUTED, fontsize=size)


def _panel(ax, x: float, y: float, w: float, h: float, *, hot: bool) -> None:
    from matplotlib.patches import FancyBboxPatch

    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.004,rounding_size=0.012",
            fc=PANEL_HOT if hot else PANEL,
            ec=WARM if hot else ACCENT,
            lw=1.8,
        )
    )


# ------------------------------------------------------------------ renderers
def _matrix(ax, spec: dict) -> None:
    """A cross-company comparison grid — the shape behind 'an Amazon L4 is not a Google L4'."""
    cols = [_clean(c) for c in spec.get("columns") or []][:4]
    rows = [r for r in spec.get("rows") or [] if isinstance(r, list | tuple)][:5]
    if not cols or not rows:
        raise ValueError("matrix needs columns and rows")
    hot = spec.get("highlight_row")
    left, span = 0.20, 0.74
    colw = span / len(cols)
    top, rowh = 0.68, min(0.15, 0.56 / max(len(rows), 1))

    for j, col in enumerate(cols):
        size = _fit_fontsize(ax, col, colw * 0.9, 19.0, 11.0)
        ax.text(
            left + colw * (j + 0.5),
            top + 0.07,
            col.upper(),
            ha="center",
            color=MUTED,
            fontsize=size,
            fontweight="bold",
        )

    for i, row in enumerate(rows):
        cells = [_clean(c) for c in row]
        label, values = (cells[0], cells[1:]) if len(cells) > len(cols) else ("", cells)
        y = top - i * rowh
        is_hot = hot is not None and i == hot
        if label:
            size = _fit_fontsize(ax, label, 0.16, 18.0, 10.0)
            ax.text(
                left - 0.03, y, label.upper(), ha="right", va="center", color=MUTED, fontsize=size
            )
        for j in range(len(cols)):
            value = values[j] if j < len(values) else ""
            _panel(
                ax, left + colw * j + 0.010, y - rowh * 0.36, colw - 0.020, rowh * 0.72, hot=is_hot
            )
            size = _fit_fontsize(ax, value, colw * 0.78, 30.0, 12.0)
            ax.text(
                left + colw * (j + 0.5),
                y,
                value,
                ha="center",
                va="center",
                color=WARM if is_hot else FG,
                fontsize=size,
                fontweight="bold",
            )


def _bars(ax, spec: dict) -> None:
    """Labelled magnitude comparison — comp bands, pass rates, latency budgets."""
    items = [i for i in spec.get("items") or [] if isinstance(i, dict)][:6]
    if not items:
        raise ValueError("bars needs items")
    values = []
    for item in items:
        try:
            values.append(float(item.get("value") or 0))
        except (TypeError, ValueError):
            values.append(0.0)
    peak = max(values) or 1.0
    top, rowh = 0.74, min(0.13, 0.62 / len(items))
    left, maxw = 0.30, 0.56

    for i, (item, value) in enumerate(zip(items, values, strict=True)):
        y = top - i * rowh
        hot = bool(item.get("highlight"))
        label = _clean(item.get("label"))
        size = _fit_fontsize(ax, label, 0.24, 20.0, 11.0)
        ax.text(left - 0.03, y, label, ha="right", va="center", color=FG, fontsize=size)
        width = max(maxw * (value / peak), 0.012)
        _panel(ax, left, y - rowh * 0.30, width, rowh * 0.60, hot=hot)
        note = _clean(item.get("note")) or _clean(item.get("value"))
        if note:
            size = _fit_fontsize(ax, note, 0.20, 18.0, 10.0)
            ax.text(
                left + width + 0.015,
                y,
                note,
                ha="left",
                va="center",
                color=WARM if hot else MUTED,
                fontsize=size,
                fontweight="bold",
            )


def _ladder(ax, spec: dict) -> None:
    """An ordered progression where each rung is visibly higher — levels, tiers, stages."""
    steps = [s for s in spec.get("steps") or [] if isinstance(s, dict)][:5]
    if not steps:
        raise ValueError("ladder needs steps")
    n = len(steps)
    boxw, boxh = 0.46, min(0.13, 0.60 / n)
    rise = min(0.155, 0.62 / n)
    # Centre the climb between the title and the caption: a fixed base leaves the ladder hugging the
    # bottom of the frame on short lists and crowding the caption line.
    base = 0.48 - ((n - 1) * rise + boxh) / 2

    for i, step in enumerate(steps):
        y = base + i * rise
        x = 0.16 + (0.30 * i / max(n - 1, 1))  # step right as well as up, so the climb reads
        hot = bool(step.get("highlight"))
        _panel(ax, x, y, boxw, boxh, hot=hot)
        label = _clean(step.get("label"))
        size = _fit_fontsize(ax, label, boxw * 0.55, 25.0, 12.0)
        ax.text(
            x + 0.022,
            y + boxh * 0.5,
            label,
            ha="left",
            va="center",
            color=WARM if hot else FG,
            fontsize=size,
            fontweight="bold",
        )
        detail = _clean(step.get("detail"))
        if detail:
            size = _fit_fontsize(ax, detail, boxw * 0.42, 16.0, 9.0)
            ax.text(
                x + boxw - 0.022,
                y + boxh * 0.5,
                detail,
                ha="right",
                va="center",
                color=MUTED,
                fontsize=size,
            )


def _flow(ax, spec: dict) -> None:
    """A small left-to-right pipeline. Capped at four nodes: beyond that the boxes get too narrow to
    read at a glance, which defeats the point of putting it on screen."""
    from matplotlib.patches import FancyArrowPatch

    nodes = [n for n in spec.get("nodes") or [] if isinstance(n, dict)][:4]
    if not nodes:
        raise ValueError("flow needs nodes")
    n = len(nodes)
    gap = 0.04
    boxw = min(0.30, (0.88 - gap * (n - 1)) / n)
    boxh = 0.24
    total = boxw * n + gap * (n - 1)
    x = (1.0 - total) / 2
    centers = []

    for node in nodes:
        hot = bool(node.get("highlight"))
        _panel(ax, x, 0.40, boxw, boxh, hot=hot)
        cx = x + boxw / 2
        centers.append(cx)
        tag = _clean(node.get("tag"))
        if tag:
            size = _fit_fontsize(ax, tag, boxw * 0.86, 17.0, 9.0)
            ax.text(
                cx,
                0.575,
                tag.upper(),
                ha="center",
                color=WARM if hot else ACCENT,
                fontsize=size,
                fontweight="bold",
            )
        name = _clean(node.get("name"))
        size = _fit_fontsize(ax, name, boxw * 0.88, 25.0, 11.0)
        ax.text(cx, 0.505, name, ha="center", color=FG, fontsize=size, fontweight="bold")
        detail = _clean(node.get("detail"))
        if detail:
            size = _fit_fontsize(ax, detail, boxw * 0.88, 17.0, 9.0)
            ax.text(cx, 0.443, detail, ha="center", color=MUTED, fontsize=size)
        x += boxw + gap

    for a, b in zip(centers, centers[1:], strict=False):
        ax.add_patch(
            FancyArrowPatch(
                (a + boxw / 2 + 0.006, 0.52),
                (b - boxw / 2 - 0.006, 0.52),
                arrowstyle="-|>",
                mutation_scale=24,
                color=WARM,
                lw=2.2,
            )
        )


_RENDERERS = {"matrix": _matrix, "bars": _bars, "ladder": _ladder, "flow": _flow}


def diagram_type(spec: Any) -> str:
    """The supported type named by ``spec``, or "" when it is unusable. Pure, so the caller can ask
    whether a shot is a diagram without touching matplotlib or the filesystem."""
    if not isinstance(spec, dict):
        return ""
    kind = _clean(spec.get("type")).lower()
    return kind if kind in _TYPES else ""


def render_diagram(spec: Any, path: str | Path, *, width: int = 1920, height: int = 1080) -> bool:
    """Draw ``spec`` to ``path``. True on success, False on ANY failure.

    Never raises: a malformed spec, a missing matplotlib, or an unwritable path must degrade to the
    normal generated-image path rather than take down a run that is otherwise fine.
    """
    kind = diagram_type(spec)
    if not kind:
        return False
    fig = None
    try:
        import matplotlib.pyplot as plt

        fig, ax = _new_axes(int(width), int(height))
        _title(ax, spec.get("title"))
        _RENDERERS[kind](ax, spec)
        _caption(ax, spec.get("caption"))
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(target, facecolor=BG)
        plt.close(fig)
        return target.exists() and target.stat().st_size > 0
    except Exception:
        if fig is not None:
            with __import__("contextlib").suppress(Exception):
                import matplotlib.pyplot as plt

                plt.close(fig)
        return False
