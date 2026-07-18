"""Avatar overlay spec — a fixed-position personal avatar composited onto every frame (future plan 1).

Deterministic and dependency-free: a pure function of the settings plus whether the avatar file
exists on disk. The renderer turns an :class:`OverlaySpec` into an ffmpeg ``overlay`` filter; when
the operator has not supplied an avatar image yet, :func:`build_overlay_spec` returns ``None`` and
rendering proceeds unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")


@dataclass(frozen=True)
class OverlaySpec:
    image_path: str  # absolute path to the avatar PNG
    position: str  # one of _POSITIONS
    scale: float  # avatar height as a fraction of the video height (0 < scale <= 1)
    margin: int  # pixels of padding from the frame edges

    def ffmpeg_xy(self) -> tuple[str, str]:
        """ffmpeg ``overlay`` x/y expressions for the chosen corner.

        Uses ffmpeg's ``main_w``/``main_h`` (base frame) and ``overlay_w``/``overlay_h``
        (avatar) so the placement is correct regardless of the avatar's scaled size.
        """
        m = max(0, int(self.margin))
        x = f"{m}" if "left" in self.position else f"main_w-overlay_w-{m}"
        y = f"{m}" if "top" in self.position else f"main_h-overlay_h-{m}"
        return x, y

    def scaled_height(self, frame_height: int) -> int:
        """Target avatar height in pixels for a given frame height (at least 1)."""
        return max(1, int(frame_height * self.scale))


def build_overlay_spec(settings) -> OverlaySpec | None:
    """Return an :class:`OverlaySpec` when the overlay is enabled and the image exists, else ``None``.

    The avatar path is resolved relative to the current working directory (the project root) when
    not absolute, so a single ``assets/avatar.png`` is reused across every run.
    """
    if not settings.avatar_overlay_enabled:
        return None
    raw = (settings.avatar_image_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.exists():
        return None
    position = settings.effective_avatar_position if settings.effective_avatar_position in _POSITIONS else "bottom-right"
    return OverlaySpec(
        image_path=str(path.resolve()),
        position=position,
        scale=float(settings.effective_avatar_scale),
        margin=int(settings.avatar_margin),
    )
