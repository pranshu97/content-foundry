"""Like nudge: a small animated, softly *glowing* "LIKE" badge shown once early in the video.

Mirrors :mod:`content_foundry.production.subscribe`: :func:`render_like_badge` draws a blue thumbs-up
pill (with a baked neon glow halo) as a transparent PNG, and :func:`build_like_spec` returns a
:class:`LikeSpec` the renderer turns into a time-gated, fading (and optionally *pulsing*) ffmpeg
overlay. Disabled -> ``build_like_spec`` returns ``None`` and rendering proceeds unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LikeSpec:
    image_path: str  # absolute path to the badge PNG
    start: float  # seconds — the badge fades in here
    duration: float  # visible seconds
    fade: float  # fade in/out seconds
    position: str  # e.g. "bottom-center", "bottom-right"
    margin: int  # pixels of padding from the frame edges
    pulse: float = 0.0  # size-pulse amplitude (0 = steady) — the gentle "glow breathing"

    @property
    def end(self) -> float:
        return self.start + self.duration

    def ffmpeg_xy(self) -> tuple[str, str]:
        """ffmpeg ``overlay`` x/y expressions for the chosen position (corners + centres). Uses
        ``overlay_w``/``overlay_h`` so the badge stays anchored even while it pulses."""
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


def _draw_thumb(draw, box: tuple[float, float, float, float], color) -> None:
    """A tidy thumbs-up drawn from primitives (no font glyphs) inside ``box``."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    r = max(1, int(min(w, h) * 0.12))
    fingers_top = y0 + h * 0.38
    # folded fingers: a rounded block filling the lower-right
    draw.rounded_rectangle([x0 + w * 0.30, fingers_top, x1, y1], radius=r, fill=color)
    # the raised thumb: a vertical capsule rising from the fingers
    tw = w * 0.30
    draw.rounded_rectangle(
        [x0 + w * 0.30, y0, x0 + w * 0.30 + tw, fingers_top + h * 0.12],
        radius=max(1, int(tw * 0.5)), fill=color,
    )
    # wrist/base at the far left
    draw.rounded_rectangle([x0, fingers_top, x0 + w * 0.22, y1], radius=r, fill=color)


def render_like_badge(target: Path, *, height_px: int = 96, glow: bool = True) -> Path:
    """Draw a blue "LIKE" pill (thumbs-up + label), optionally with a baked neon glow halo, as a
    transparent RGBA PNG at ``target``. With ``glow`` the canvas is padded so the halo has room."""
    from PIL import Image, ImageDraw, ImageFilter

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    h = max(24, int(height_px))
    pad = int(h * 0.28)
    font = _load_font(int(h * 0.42))
    label = "LIKE"

    measure = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tb = measure.textbbox((0, 0), label, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    thumb = int(h * 0.5)
    gap = int(h * 0.18)
    w = pad + thumb + gap + tw + pad

    blue = (6, 95, 212, 255)
    white = (255, 255, 255, 255)
    pill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(pill)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(h * 0.28), fill=blue)
    _draw_thumb(draw, (pad, h * 0.22, pad + thumb, h * 0.22 + thumb), white)
    draw.text((pad + thumb + gap, (h - th) // 2 - tb[1]), label, font=font, fill=white)

    glow_pad = int(h * 0.5) if glow else 0
    canvas = Image.new("RGBA", (w + 2 * glow_pad, h + 2 * glow_pad), (0, 0, 0, 0))
    if glow:
        halo = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle(
            [glow_pad, glow_pad, glow_pad + w - 1, glow_pad + h - 1],
            radius=int(h * 0.28), fill=(90, 170, 255, 255),
        )
        halo = halo.filter(ImageFilter.GaussianBlur(radius=max(2, int(h * 0.20))))
        canvas.alpha_composite(halo)
    canvas.alpha_composite(pill, (glow_pad, glow_pad))
    canvas.save(target, format="PNG")
    return target


def build_like_spec(settings, *, run_root, total_duration: float, frame_height: int):
    """Return a :class:`LikeSpec` (generating the badge PNG) when the nudge is enabled and the video
    is long enough to hold it, else ``None``. Placed at ``like_nudge_at`` of the runtime so it never
    collides with the midpoint Subscribe badge."""
    if not settings.like_nudge_enabled:
        return None
    duration = float(settings.like_nudge_sec)
    if total_duration <= duration + 1.0:  # too short to be worth interrupting
        return None
    height_px = max(32, int(frame_height * 0.09))
    badge = Path(run_root) / "assets" / "like_badge.png"
    render_like_badge(badge, height_px=height_px, glow=settings.like_nudge_glow)
    at = min(max(float(settings.like_nudge_at), 0.05), 0.9)
    start = max(0.0, total_duration * at - duration / 2.0)
    start = min(start, max(0.0, total_duration - duration - 0.5))  # keep clear of the very end
    return LikeSpec(
        image_path=str(badge.resolve()),
        start=round(start, 3),
        duration=duration,
        fade=round(min(0.5, duration / 3.0), 3),
        position=settings.like_nudge_position,
        margin=int(frame_height * 0.04),
        pulse=float(settings.like_nudge_pulse) if settings.like_nudge_glow else 0.0,
    )
