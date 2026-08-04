"""Camera motion for STILL shots, so a generated image sits beside real footage without freezing.

A still dropped between moving B-roll clips reads as a dead frame — the cut to it feels like the
video stalled. Giving it a slow push, pan or tilt is what documentary editors do with photographs,
and it is invisible when the move matches the composition: you push into a macro detail, you tilt up
a tall frame, you track across a row of things. Pick the wrong move and it is very visible (panning
across a macro close-up is nauseating), which is why the move is chosen from the shot's own prompt
rather than at random — the image director already states the camera strategy it composed for
("an extreme macro", "an overhead flat-lay", "a low-angle architectural shot").

Deliberately motion ONLY. Hue/brightness/vignette modulation has no real-world referent, so it can
only read as "an effect was applied"; worse, applying it to the stills alone would make them visibly
diverge from the surrounding clips, which is the opposite of blending in.
"""

from __future__ import annotations

# Travel of a push/pull over the WHOLE shot. Beats run 10-15s, so a small number here is spread very
# thin — 8% was measurably moving but read as static to the eye. Still bounded: the frame must not
# recompose enough to crop the calm area the on-screen caption sits in.
ZOOM_TRAVEL = 0.18
# Fixed zoom held during a pan/tilt; the surplus over 1.0 is the room the frame travels through.
PAN_ZOOM = 1.16
# Rendering a still at this multiple of the output before the move keeps the pan smooth: zoompan
# steps x/y in WHOLE pixels, so at 1x a slow pan visibly stutters. Oversampling makes each step
# sub-pixel in the delivered frame, and it keeps a zoom from softening the image.
OVERSAMPLE = 2

PUSH_IN = "push_in"
PULL_OUT = "pull_out"
PAN_LEFT = "pan_left"
PAN_RIGHT = "pan_right"
TILT_UP = "tilt_up"
TILT_DOWN = "tilt_down"
KEN_BURNS = "ken_burns"
NONE = "none"

# Tried in order; the FIRST match wins, so the most specific composition is listed first. Each rule
# lists its acceptable moves in preference order — when the first would repeat the previous shot's
# move, the NEXT one from the SAME rule is used. That matters: falling back to a generic rotation
# would hand a macro frame a lateral pan, which is the one move a macro must never get.
_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    # A macro frame has almost no headroom and a pan across one is unwatchable — zooms only.
    ((PUSH_IN, PULL_OUT), ("macro", "extreme close", "razor-thin", "close-up", "tight, ")),
    # The frame already points upward, so continuing the move up feels like the same camera.
    ((TILT_UP, PUSH_IN), ("looking up", "low-angle architectural", "ceiling", "soaring", "up at")),
    # Pulling back off a flat-lay reveals the arrangement, which is the point of shooting it flat.
    ((PULL_OUT, PUSH_IN), ("flat-lay", "flat lay", "overhead", "top-down", "bird's-eye")),
    # A subject laid out ACROSS the frame wants a lateral move to travel along it.
    ((PAN_RIGHT, PAN_LEFT), ("row of", "bank of", "wall of", "rack", "corridor", "long table")),
    # Depth running away from camera reads best when you move into it.
    ((PUSH_IN, KEN_BURNS), ("wide", "establishing", "receding", "vast", "expanse")),
    # Looking through something is a lateral reveal.
    ((PAN_LEFT, PAN_RIGHT), ("through glass", "through a glass", "partition", "window")),
)

# Used when nothing matches, and to break up runs of the same move.
_CYCLE = (PUSH_IN, PAN_RIGHT, PULL_OUT, TILT_UP, PAN_LEFT, KEN_BURNS)


def pick_motion(prompt: str, *, index: int = 0, previous: str = "") -> str:
    """Choose the camera move for a still from the prompt that composed it.

    ``index`` only breaks ties (so a scene of unmatched shots still varies), and ``previous`` stops
    the same move landing twice in a row, which is what makes a sequence read as a tic — but never
    at the cost of giving a composition a move that fights it.
    """
    text = (prompt or "").lower()
    for motions, needles in _RULES:
        if any(n in text for n in needles):
            for motion in motions:
                if motion != previous:
                    return motion
            return motions[0]  # every safe option repeats; the right move still beats a wrong one
    for offset in range(len(_CYCLE)):
        motion = _CYCLE[(index + offset) % len(_CYCLE)]
        if motion != previous:
            return motion
    return _CYCLE[index % len(_CYCLE)]


def motion_expressions(motion: str, frames: int) -> tuple[str, str, str] | None:
    """``(z, x, y)`` zoompan expressions for ``motion``, or None when the shot should stay still.

    ``frames`` is the shot's total output frames; progress is ``on/(frames-1)`` so the move starts and
    ends exactly on the shot, never mid-travel. ``z`` never drops below 1.0 — zoompan cannot show
    anything outside the source, so a smaller zoom would letterbox the frame.
    """
    span = max(int(frames) - 1, 1)
    p = f"(on/{span})"  # 0 -> 1 across the shot
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    # Full travel available at PAN_ZOOM, mapped over the shot.
    travel_x, travel_y = f"(iw-iw/zoom)*{p}", f"(ih-ih/zoom)*{p}"
    back_x, back_y = f"(iw-iw/zoom)*(1-{p})", f"(ih-ih/zoom)*(1-{p})"
    if motion == PUSH_IN:
        return (f"1+{ZOOM_TRAVEL}*{p}", centre_x, centre_y)
    if motion == PULL_OUT:
        return (f"{1 + ZOOM_TRAVEL}-{ZOOM_TRAVEL}*{p}", centre_x, centre_y)
    if motion == PAN_RIGHT:
        return (str(PAN_ZOOM), travel_x, centre_y)
    if motion == PAN_LEFT:
        return (str(PAN_ZOOM), back_x, centre_y)
    if motion == TILT_DOWN:
        return (str(PAN_ZOOM), centre_x, travel_y)
    if motion == TILT_UP:
        return (str(PAN_ZOOM), centre_x, back_y)
    if motion == KEN_BURNS:
        # Push in while drifting across, the classic archive-photograph move.
        return (f"1+{ZOOM_TRAVEL}*{p}", f"(iw-iw/zoom)*0.5*{p}", f"(ih-ih/zoom)*0.5*{p}")
    return None
