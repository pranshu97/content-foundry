"""Still-image camera motion: a generated image must not sit frozen between moving B-roll clips."""

from __future__ import annotations

import pytest

from content_foundry.production.motion import (
    KEN_BURNS,
    NONE,
    PAN_LEFT,
    PAN_RIGHT,
    PULL_OUT,
    PUSH_IN,
    TILT_UP,
    ZOOM_TRAVEL,
    motion_expressions,
    pick_motion,
)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        # A macro frame has no headroom and panning one is unwatchable -> push instead.
        ("An extreme macro shot of a fibre patch panel, razor-thin depth of field", PUSH_IN),
        ("A tight, shallow-focus close-up of a folder", PUSH_IN),
        # The frame already points up, so keep the camera going that way.
        ("A wide, low-angle architectural shot looking up at a lattice ceiling", TILT_UP),
        # Pulling back off a flat-lay reveals the arrangement, which is why it was shot flat.
        ("A high-angle, top-down flat-lay of evaluation binders on slate", PULL_OUT),
        # A subject laid ACROSS frame wants a lateral move along it.
        ("A row of empty ergonomic chairs lining a long table", PAN_RIGHT),
        ("A corridor of server racks receding into the distance", PAN_RIGHT),
        # Looking through something is a lateral reveal.
        ("Shot through glass into a private office, reflection across the pane", PAN_LEFT),
    ],
)
def test_motion_matches_what_the_image_actually_is(prompt, expected):
    """The image director states the camera it composed for, so the move can match the composition
    instead of being random — that is what makes it read as one continuous piece of footage."""
    assert pick_motion(prompt) == expected


def test_unmatched_prompts_still_vary_across_a_scene():
    # Nothing recognisable in the text -> cycle, so a scene of plain prompts doesn't repeat one move.
    picks = [pick_motion("a picture of something", index=i) for i in range(6)]
    assert len(set(picks)) > 1


def test_never_repeats_the_previous_move():
    """The same move twice running is what turns a technique into a visible tic."""
    assert pick_motion("an extreme macro of a key", previous=PUSH_IN) != PUSH_IN
    for i in range(12):
        assert pick_motion("nothing recognisable", index=i, previous=PAN_RIGHT) != PAN_RIGHT


def test_avoiding_a_repeat_never_gives_a_composition_a_move_that_fights_it():
    """Breaking up a run must not override the composition: a macro frame has no headroom, so the
    alternative has to be the other ZOOM, never a lateral pan across it."""
    assert pick_motion("An extreme macro shot of a gauge", previous=PUSH_IN) == PULL_OUT
    assert pick_motion("An extreme macro shot of a gauge", previous=PULL_OUT) == PUSH_IN
    # A flat-lay likewise stays on zooms rather than sliding sideways.
    assert pick_motion("An overhead flat-lay of tiles", previous=PULL_OUT) == PUSH_IN
    # A lateral subject stays lateral — it just changes direction.
    assert pick_motion("A corridor of server racks", previous=PAN_RIGHT) == PAN_LEFT


def test_expressions_start_and_end_on_the_shot():
    z, x, y = motion_expressions(PUSH_IN, frames=100)
    assert "on/99" in z  # progress spans the shot exactly, so the move never stops mid-travel
    assert x and y


def test_zoom_never_goes_below_one():
    """zoompan cannot show anything outside the source, so z < 1 would letterbox the frame."""
    for motion in (PUSH_IN, PULL_OUT, PAN_LEFT, PAN_RIGHT, TILT_UP, KEN_BURNS):
        z, _, _ = motion_expressions(motion, frames=60)
        # Evaluate the expression at both ends of the shot.
        for on in (0, 59):
            value = eval(z.replace("on", str(on)))  # noqa: S307 - our own generated arithmetic
            assert value >= 1.0, f"{motion} zooms below 1.0 ({value})"


def test_travel_is_subtle_enough_to_keep_the_composition():
    # Beats run 10-15s so the move must be large enough to read, but a big push recomposes the shot
    # and can crop out the calm area the caption sits in.
    assert 0.10 <= ZOOM_TRAVEL <= 0.25
    z, _, _ = motion_expressions(PUSH_IN, frames=2)
    assert eval(z.replace("on", "1")) == pytest.approx(1 + ZOOM_TRAVEL)  # noqa: S307


def test_no_motion_returns_nothing_to_apply():
    assert motion_expressions(NONE, frames=30) is None
    assert motion_expressions("", frames=30) is None


def test_still_filter_graph_uses_real_numbers_for_string_dimensions():
    """The renderer splits "1920x1080" and passes the halves as STRINGS, so the oversample must
    coerce them: "1920" * 2 is "19201920", which ffmpeg rejects as a picture size ("Picture size
    19201920x10801080 is invalid") instead of failing where the mistake is. Compiling the graph
    catches it without needing the ffmpeg binary.
    """
    ffmpeg = pytest.importorskip("ffmpeg")

    from content_foundry.production.motion import OVERSAMPLE
    from content_foundry.providers.render_backend import _still_stream

    stream = _still_stream("shot.png", "1920", "1080", 30, 2.0, PUSH_IN)
    args = " ".join(ffmpeg.output(stream, "out.mp4").get_args())
    assert f"{1920 * OVERSAMPLE}" in args and f"{1080 * OVERSAMPLE}" in args
    assert "19201920" not in args and "10801080" not in args
    assert "1920x1080" in args  # zoompan still delivers the real output size


def test_still_filter_graph_is_built_for_a_static_shot_too():
    ffmpeg = pytest.importorskip("ffmpeg")

    from content_foundry.providers.render_backend import _still_stream

    args = " ".join(
        ffmpeg.output(_still_stream("s.png", "1920", "1080", 30, 2.0, NONE), "o.mp4").get_args()
    )
    assert "zoompan" not in args  # a shot with no move must not pay for the filter
    assert "19201920" not in args


def test_single_frame_shot_does_not_divide_by_zero():
    for motion in (PUSH_IN, PAN_RIGHT, KEN_BURNS):
        assert motion_expressions(motion, frames=1) is not None
