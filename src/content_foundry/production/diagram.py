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

# The vertical band the CONTENT lives in, between the title and the caption. Every renderer centres
# its block inside this band AND scales the block to fill it. Before this, each renderer anchored to
# its own fixed top and grew downward, so a two-row matrix and a five-row matrix both began at the
# same y and the short one left a void beneath -- MEASURED on run 0024, all seven diagrams started
# their ink on the identical pixel row and every one of them had a completely empty stripe at
# y 0.7-0.8. That constant silhouette is what made different diagrams read as one picture.
CONTENT_TOP = 0.82
CONTENT_BOTTOM = 0.18
CONTENT_BAND = CONTENT_TOP - CONTENT_BOTTOM

# A ladder rung carries a LEFT-aligned label and a RIGHT-aligned detail inside one box. Their width
# budgets must leave room for the padding at BOTH ends plus a gap between them, or the two strings
# meet in the middle -- which is exactly what "ML Infra Work" and "Search pipelines & feature
# layers" did on run 0024, because the old budgets summed to 0.97 of the box. Each string is fitted
# independently, so nothing else stops them colliding: keep this sum comfortably under 1.0.
_RUNG_PAD = 0.022
_RUNG_LABEL_W = 0.46
_RUNG_DETAIL_W = 0.34


def _clean(value: Any) -> str:
    """Spec text comes from an LLM, so coerce and strip rather than trusting the type."""
    return " ".join(str(value or "").split())


def _measure(ax, renderer, text: str, size: float) -> float:
    """Width of ``text`` at ``size`` as a fraction of the figure width."""
    probe = ax.text(0.5, 0.5, text, fontsize=size, ha="center", va="center")
    width = probe.get_window_extent(renderer).width / ax.figure.bbox.width
    probe.remove()
    return width


def _fit_fontsize(ax, text: str, max_frac: float, start: float, floor: float = 9.0) -> float:
    """Largest font size at which ``text`` fits ``max_frac`` of the axes width.

    This is the whole reason matplotlib won over graphviz: real metrics mean a label can be measured
    and shrunk instead of silently overflowing its box. Shrinks rather than truncates, because a
    clipped word reads as a rendering bug while slightly smaller type just reads as design.

    NOTE it returns ``floor`` when even that does not fit, and the caller draws at that size anyway.
    For text sitting INSIDE a panel use ``_fit_wrapped``, which wraps before that can happen.
    """
    if not text:
        return start
    fig = ax.figure
    fig.canvas.draw()  # a renderer must exist before anything can be measured
    renderer = fig.canvas.get_renderer()
    size = start
    while size > floor:
        if _measure(ax, renderer, text, size) <= max_frac:
            return size
        size -= 1.0
    return floor


def _split_two(text: str) -> str:
    """Break ``text`` at the word boundary that leaves the two lines most even."""
    words = text.split()
    if len(words) < 2:
        return text
    best, best_cost = text, None
    for i in range(1, len(words)):
        head, tail = " ".join(words[:i]), " ".join(words[i:])
        cost = abs(len(head) - len(tail))
        if best_cost is None or cost < best_cost:
            best, best_cost = f"{head}\n{tail}", cost
    return best


def _fit_wrapped(ax, text: Any, max_frac: float, start: float, floor: float = 9.0):
    """Fit text inside a panel, WRAPPING to a second line rather than letting it overflow.

    ``_fit_fontsize`` returns its floor when nothing fits and the caller then draws at that size
    regardless, so an over-long label silently ran outside its box -- on run 0024 one matrix cell
    ran clean off the right edge of the frame (ink at x=0.999). Wrapping is what a designer would
    do, and it keeps the type large enough to read instead of shrinking it into nothing.

    Returns ``(text, size)``; the text may contain a newline.
    """
    text = _clean(text)
    if not text:
        return text, start
    size = _fit_fontsize(ax, text, max_frac, start, floor)
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    if _measure(ax, renderer, text, size) <= max_frac:
        return text, size
    wrapped = _split_two(text)
    if wrapped == text:  # a single unbreakable word: nothing to wrap, keep the smallest type
        return text, size
    lines = wrapped.split("\n")
    probe = start
    while probe > floor:
        if max(_measure(ax, renderer, line, probe) for line in lines) <= max_frac:
            return wrapped, probe
        probe -= 1.0
    return wrapped, floor


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
    # Centre the grid in the content band and let the rows GROW to fill it, so a two-row matrix is a
    # visibly different picture from a five-row one instead of both hugging the same fixed top.
    header = 0.055
    rowh = min(0.24, (CONTENT_BAND - header) / max(len(rows), 1))
    block = len(rows) * rowh + header
    top = CONTENT_BOTTOM + (CONTENT_BAND - block) / 2 + block - header - rowh / 2

    for j, col in enumerate(cols):
        size = _fit_fontsize(ax, col, colw * 0.9, 19.0, 11.0)
        ax.text(
            left + colw * (j + 0.5),
            top + rowh * 0.5 + 0.028,
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
            value, size = _fit_wrapped(ax, value, colw * 0.78, 30.0, 12.0)
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
    rowh = min(0.20, CONTENT_BAND / len(items))
    top = CONTENT_BOTTOM + (CONTENT_BAND - rowh * len(items)) / 2 + rowh * len(items) - rowh / 2
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
    boxw = 0.46
    # Centre the climb in the content band and let it use the whole height: a short ladder should
    # read as big confident rungs, not as a small one stranded in the middle of an empty frame.
    boxh = min(0.16, CONTENT_BAND / (n + 0.6))
    rise = min(0.19, (CONTENT_BAND - boxh) / max(n - 1, 1))
    base = CONTENT_BOTTOM + (CONTENT_BAND - ((n - 1) * rise + boxh)) / 2

    for i, step in enumerate(steps):
        y = base + i * rise
        x = 0.16 + (0.30 * i / max(n - 1, 1))  # step right as well as up, so the climb reads
        hot = bool(step.get("highlight"))
        _panel(ax, x, y, boxw, boxh, hot=hot)
        label = _clean(step.get("label"))
        size = _fit_fontsize(ax, label, boxw * _RUNG_LABEL_W, 25.0, 12.0)
        ax.text(
            x + _RUNG_PAD,
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
            size = _fit_fontsize(ax, detail, boxw * _RUNG_DETAIL_W, 16.0, 9.0)
            ax.text(
                x + boxw - _RUNG_PAD,
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
    # Taller boxes, centred in the band: the old fixed 0.24-high strip at y=0.40 left a third of the
    # frame empty above and below, which is most of why every flow looked like every other one.
    boxh = 0.34
    boxy = CONTENT_BOTTOM + (CONTENT_BAND - boxh) / 2
    mid = boxy + boxh / 2
    total = boxw * n + gap * (n - 1)
    x = (1.0 - total) / 2
    centers = []

    for node in nodes:
        hot = bool(node.get("highlight"))
        _panel(ax, x, boxy, boxw, boxh, hot=hot)
        cx = x + boxw / 2
        centers.append(cx)
        tag = _clean(node.get("tag"))
        if tag:
            size = _fit_fontsize(ax, tag, boxw * 0.86, 17.0, 9.0)
            ax.text(
                cx,
                boxy + boxh * 0.76,
                tag.upper(),
                ha="center",
                va="center",
                color=WARM if hot else ACCENT,
                fontsize=size,
                fontweight="bold",
            )
        name, size = _fit_wrapped(ax, node.get("name"), boxw * 0.88, 25.0, 11.0)
        ax.text(
            cx,
            boxy + boxh * 0.50,
            name,
            ha="center",
            va="center",
            color=FG,
            fontsize=size,
            fontweight="bold",
        )
        detail, size = _fit_wrapped(ax, node.get("detail"), boxw * 0.88, 17.0, 9.0)
        if detail:
            ax.text(
                cx, boxy + boxh * 0.22, detail, ha="center", va="center", color=MUTED, fontsize=size
            )
        x += boxw + gap

    for a, b in zip(centers, centers[1:], strict=False):
        ax.add_patch(
            FancyArrowPatch(
                (a + boxw / 2 + 0.006, mid),
                (b - boxw / 2 - 0.006, mid),
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
