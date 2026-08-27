"""A real generated thumbnail ships AS-IS; only the fallback card carries overlay text.

The thumbnail director now writes a finished frame (it composes the scene's own in-image labels), so
stamping a title over it covers the picture it was asked to make. But the fallback path has no AI
image at all -- just an abstract designed gradient -- so text there is the only thing that makes the
frame mean anything, and it must NOT be dropped along with it.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from content_foundry.agents import visuals as visuals_mod
from content_foundry.agents.visuals import Visuals, _write_card


@pytest.fixture
def card_calls(monkeypatch):
    """Capture what text _compose_thumbnail asks the card renderer to draw."""
    calls: list[dict] = []
    real = visuals_mod._write_card

    def spy(text, size_wh, target, base_png=None, **kw):
        calls.append({"text": text, "has_image": base_png is not None})
        return real(text, size_wh, target, base_png=base_png, **kw)

    monkeypatch.setattr(visuals_mod, "_write_card", spy)
    return calls


class _NoImage:
    """An image provider whose generation always fails, forcing the fallback card."""

    name = "none"

    def generate(self, prompt: str, size: str = "1024x1024") -> bytes:
        raise RuntimeError("no image provider")


def test_a_generated_thumbnail_gets_no_overlay_text(
    settings, good_script, tmp_path, fakes, card_calls
):
    text = Visuals(settings, fakes.Image(), None).render_thumbnail(good_script, run_root=tmp_path)
    thumb = [c for c in card_calls if c["has_image"]]
    assert thumb, "expected the thumbnail to be drawn over a generated image"
    assert thumb[-1]["text"] == "", "overlay text was stamped over the generated thumbnail"
    # The text is still COMPUTED and returned - it is real metadata, and the fallback path needs it.
    assert text


def test_the_fallback_card_still_carries_its_text(settings, good_script, tmp_path, card_calls):
    """With no usable image the card is all there is; dropping the text would ship a bare gradient."""
    Visuals(settings, _NoImage(), None).render_thumbnail(good_script, run_root=tmp_path)
    fallback = [c for c in card_calls if not c["has_image"]]
    assert fallback, "expected a fallback card when image generation fails"
    assert fallback[-1]["text"].strip(), "the fallback card lost its text and would render blank"


def test_blank_text_never_becomes_the_watch_this_placeholder(tmp_path):
    """_draw_punchy_title substitutes 'WATCH THIS' for empty text, so the call must be SKIPPED."""
    base = Image.new("RGB", (64, 36), (120, 60, 30))
    from io import BytesIO

    buf = BytesIO()
    base.save(buf, format="PNG")
    raw = buf.getvalue()

    blank = tmp_path / "blank.png"
    titled = tmp_path / "titled.png"
    _write_card("", (64, 36), blank, base_png=raw, punchy=True)
    _write_card("WATCH THIS", (64, 36), titled, base_png=raw, punchy=True)

    a = np.asarray(Image.open(blank).convert("RGB")).astype(int)
    b = np.asarray(Image.open(titled).convert("RGB")).astype(int)
    assert np.abs(a - b).mean() > 1.0, "blank text still drew the WATCH THIS placeholder"
