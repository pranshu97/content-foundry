"""Subscribe nudge: a small animated "Subscribe" badge shown at the video's midpoint (Ch. 12.5).

Deterministic and dependency-light: :func:`render_subscribe_badge` draws a red pill (bell +
SUBSCRIBE) as a transparent PNG, and :func:`build_subscribe_spec` returns a :class:`SubscribeSpec`
the renderer turns into a time-gated, fading ffmpeg overlay. Disabled -> ``build_subscribe_spec``
returns ``None`` and rendering proceeds unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SubscribeSpec:
    image_path: str  # absolute path to the badge PNG
    start: float  # seconds — the badge fades in here (centred on the midpoint)
    duration: float  # visible seconds
    fade: float  # fade in/out seconds
    position: str  # e.g. "bottom-center", "bottom-right"
    margin: int  # pixels of padding from the frame edges

    @property
    def end(self) -> float:
        return self.start + self.duration

    def ffmpeg_xy(self) -> tuple[str, str]:
        """ffmpeg ``overlay`` x/y expressions for the chosen position (corners + centres)."""
        m = max(0, int(self.margin))
        pos = self.position
        if "left" in pos:
            x = f"{m}"
        elif "right" in pos:
            x = f"main_w-overlay_w-{m}"
        else:  # centre horizontally
            x = "(main_w-overlay_w)/2"
        if "top" in pos:
            y = f"{m}"
        elif "bottom" in pos:
            y = f"main_h-overlay_h-{m}"
        else:  # centre vertically
            y = "(main_h-overlay_h)/2"
        return x, y


def _load_font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)  # scalable DejaVu (Pillow >= 10.1)
    except Exception:  # pragma: no cover - ancient Pillow / no freetype
        return ImageFont.load_default()


def _draw_bell(draw, box: tuple[float, float, float, float], color) -> None:
    """A tidy little bell drawn from primitives (no font glyphs) inside ``box``."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx = x0 + w / 2
    r = max(1.0, w * 0.08)
    draw.ellipse([cx - r, y0, cx + r, y0 + 2 * r], fill=color)  # top knob
    body_top = y0 + 1.6 * r
    body_bottom = y0 + h * 0.72
    bw = w * 0.6
    draw.ellipse([cx - bw / 2, body_top, cx + bw / 2, body_top + bw], fill=color)  # dome
    draw.rectangle([cx - bw / 2, body_top + bw / 2, cx + bw / 2, body_bottom], fill=color)  # body
    draw.polygon(  # flared rim
        [
            (x0 + w * 0.05, body_bottom + 1),
            (x1 - w * 0.05, body_bottom + 1),
            (cx + bw * 0.55, body_bottom - h * 0.06),
            (cx - bw * 0.55, body_bottom - h * 0.06),
        ],
        fill=color,
    )
    cr = max(1.0, w * 0.09)
    draw.ellipse([cx - cr, body_bottom + 1, cx + cr, body_bottom + 1 + 2 * cr], fill=color)  # clapper


def render_subscribe_badge(target: Path, *, height_px: int = 96) -> Path:
    """Draw a red "Subscribe" pill (bell + label) as a transparent RGBA PNG at ``target``."""
    from PIL import Image, ImageDraw

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    h = max(24, int(height_px))
    pad = int(h * 0.28)
    font = _load_font(int(h * 0.42))
    label = "SUBSCRIBE"

    measure = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tb = measure.textbbox((0, 0), label, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    bell = int(h * 0.5)
    gap = int(h * 0.18)
    w = pad + bell + gap + tw + pad

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    red = (204, 0, 0, 255)
    white = (255, 255, 255, 255)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(h * 0.28), fill=red)
    _draw_bell(draw, (pad, h * 0.24, pad + bell, h * 0.24 + bell), white)
    draw.text((pad + bell + gap, (h - th) // 2 - tb[1]), label, font=font, fill=white)
    img.save(target, format="PNG")
    return target


def build_subscribe_spec(settings, *, run_root, total_duration: float, frame_height: int):
    """Return a :class:`SubscribeSpec` (generating the badge PNG) when the nudge is enabled and the
    video is long enough to hold it, else ``None``."""
    if not settings.subscribe_nudge_enabled:
        return None
    duration = float(settings.subscribe_nudge_sec)
    if total_duration <= duration + 1.0:  # too short to be worth interrupting
        return None
    height_px = max(32, int(frame_height * 0.09))
    badge = Path(run_root) / "assets" / "subscribe_badge.png"
    render_subscribe_badge(badge, height_px=height_px)
    start = max(0.0, total_duration / 2.0 - duration / 2.0)
    return SubscribeSpec(
        image_path=str(badge.resolve()),
        start=round(start, 3),
        duration=duration,
        fade=round(min(0.5, duration / 3.0), 3),
        position=settings.subscribe_nudge_position,
        margin=int(frame_height * 0.04),
    )
