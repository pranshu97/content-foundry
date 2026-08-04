"""Agent 5 (Visuals) tests: deterministic prompts, cards, captions, B-roll (Ch. 11)."""

from __future__ import annotations

from content_foundry.agents import Visuals, build_image_prompt
from content_foundry.models import (
    Provenance,
    SceneTiming,
    VoiceoverAsset,
    WordTiming,
)


def _voiceover(script) -> VoiceoverAsset:
    scene_timings = [
        SceneTiming(scene_index=s.index, start=float(s.index * 3), end=float(s.index * 3 + 3))
        for s in script.scenes
    ]
    words = [
        WordTiming(word=w, start=float(i), end=float(i) + 0.4)
        for i, w in enumerate(script.hook.split())
    ]
    return VoiceoverAsset(
        run_id="R",
        audio_path="assets/narration.mp3",
        duration_sec=float(len(script.scenes) * 3),
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=words,
        scene_timings=scene_timings,
        provenance=Provenance(produced_by="voiceover"),
    )


def test_build_image_prompt_is_pure():
    p1 = build_image_prompt(["closed door", "job board"], "BIG TEXT", "clean infographic")
    p2 = build_image_prompt(["closed door", "job board"], "BIG TEXT", "clean infographic")
    assert p1 == p2
    assert "clean infographic" in p1 and "BIG TEXT" in p1 and "closed door, job board" in p1
    assert "no real people" in p1


def test_thumbnail_text_capped_and_emotion_detected():
    from content_foundry.agents.visuals import _cap_words, _detect_emotion

    # Word cap keeps a thumbnail overlay scannable (<= N big words); short lines pass through.
    assert _cap_words("ONE TWO THREE FOUR FIVE SIX SEVEN", 5) == "ONE TWO THREE FOUR FIVE"
    assert _cap_words("STOP APPLYING WRONG", 5) == "STOP APPLYING WRONG"
    # Emotion detection drives the avatar_<emotion>.png variant choice (else the base avatar).
    assert _detect_emotion("a shocked person staring at a laptop, red X") == "shocked"
    assert _detect_emotion("dramatic blue and red split lighting, bold") == ""  # none named


def test_thumbnail_prompt_is_saved_edited_and_overridable(settings, good_script, tmp_path):
    from io import BytesIO

    from PIL import Image

    class _RecordingImage:
        name = "fake-image"

        def __init__(self):
            self.last_prompt = None

        def generate(self, prompt, size):
            self.last_prompt = prompt
            buf = BytesIO()
            Image.new("RGB", (16, 9), "black").save(buf, "PNG")
            return buf.getvalue()

    # First render builds the prompt from the concept and saves the EXACT prompt used to a file.
    img = _RecordingImage()
    Visuals(settings, img, None).render_thumbnail(good_script, run_root=tmp_path)
    prompt_file = tmp_path / "assets" / "thumbnail_prompt.txt"
    assert prompt_file.exists() and prompt_file.read_text(encoding="utf-8") == img.last_prompt
    # An EDITED prompt file is used verbatim on the next render (full manual control).
    prompt_file.write_text("MY HAND-TUNED THUMBNAIL PROMPT")
    img2 = _RecordingImage()
    Visuals(settings, img2, None).render_thumbnail(good_script, run_root=tmp_path)
    assert img2.last_prompt == "MY HAND-TUNED THUMBNAIL PROMPT"
    # An explicit override wins over the saved file and is itself saved back.
    img3 = _RecordingImage()
    Visuals(settings, img3, None).render_thumbnail(
        good_script, run_root=tmp_path, prompt="EXPLICIT OVERRIDE"
    )
    assert img3.last_prompt == "EXPLICIT OVERRIDE"
    assert prompt_file.read_text(encoding="utf-8") == "EXPLICIT OVERRIDE"


def test_ensure_upload_safe_thumbnail_shrinks_oversize(tmp_path):
    import os

    from PIL import Image

    from content_foundry.agents.visuals import ensure_upload_safe_thumbnail

    p = tmp_path / "thumb.png"
    Image.frombytes("RGB", (1280, 720), os.urandom(1280 * 720 * 3)).save(p, "PNG")
    assert p.stat().st_size > 1_900_000  # a full-noise 720p PNG blows past YouTube's 2 MB limit
    # A MANUALLY-swapped oversize thumbnail is re-optimized/downscaled in place so thumbnails.set
    # accepts it — the fix for a custom thumbnail silently failing to upload.
    assert ensure_upload_safe_thumbnail(p) is True
    assert p.stat().st_size <= 1_900_000
    # Now under the limit it's left untouched; a missing file is a no-op and never raises.
    assert ensure_upload_safe_thumbnail(p) is False
    assert ensure_upload_safe_thumbnail(tmp_path / "nope.png") is False


def test_thumbnail_fallback_bg_is_a_designed_nonempty_frame():
    # When every image provider is down, the thumbnail must still be a full, DESIGNED frame (glow +
    # gradient + tech dots + accent), never a flat/near-empty dark rectangle.
    from content_foundry.agents.visuals import _gradient_bg, _thumbnail_fallback_bg

    bg = _thumbnail_fallback_bg((1280, 720))
    assert bg.size == (1280, 720) and bg.mode == "RGB"
    colors = bg.getcolors(maxcolors=200000)
    assert colors is not None and len(colors) > 500  # rich, not a flat fill
    assert list(bg.getdata()) != list(
        _gradient_bg((1280, 720)).getdata()
    )  # richer than a plain gradient


def test_visuals_render_cards_and_captions(settings, good_script, tmp_path):
    vo = _voiceover(good_script)
    pkg = Visuals(settings, image_provider=None, broll_client=None).run(
        "R", good_script, vo, run_root=tmp_path
    )
    assert (tmp_path / "assets" / "thumbnail.png").exists()
    assert (tmp_path / "assets" / "captions.srt").exists()
    assert pkg.scenes and all(sv.kind == "image" and sv.source == "card" for sv in pkg.scenes)
    for sv in pkg.scenes:
        assert (tmp_path / sv.path).exists()


def test_visuals_use_broll_when_available(settings, good_script, tmp_path, fakes):
    vo = _voiceover(good_script)
    pkg = Visuals(settings, image_provider=None, broll_client=fakes.Broll()).run(
        "R", good_script, vo, run_root=tmp_path
    )
    assert any(sv.kind == "broll" and sv.source == "pexels" for sv in pkg.scenes)


def test_broll_clips_capped_at_two_uses(settings, good_script, tmp_path, fakes):
    # Only 2 clips available for a multi-scene script -> no clip downloaded more than twice.
    from collections import Counter

    broll = fakes.Broll(urls=["https://x/a.mp4", "https://x/b.mp4"])
    Visuals(settings, image_provider=None, broll_client=broll).run(
        "R", good_script, _voiceover(good_script), run_root=tmp_path
    )
    counts = Counter(broll.downloaded)
    assert counts and all(c <= 2 for c in counts.values())


def test_broll_prefers_fresh_clips(settings, good_script, tmp_path, fakes):
    # With a large pool, each scene gets a distinct clip (no repeats).
    broll = fakes.Broll()  # 10 distinct clips
    Visuals(settings, image_provider=None, broll_client=broll).run(
        "R", good_script, _voiceover(good_script), run_root=tmp_path
    )
    assert broll.downloaded and len(set(broll.downloaded)) == len(broll.downloaded)


def test_visuals_use_image_provider(settings, good_script, tmp_path, fakes):
    vo = _voiceover(good_script)
    image = fakes.Image()
    pkg = Visuals(settings, image_provider=image, broll_client=None).run(
        "R", good_script, vo, run_root=tmp_path
    )
    assert image.calls >= 1
    assert any(sv.source == "fake-image" for sv in pkg.scenes)


def test_visuals_generate_image_when_a_beat_has_no_broll(settings, good_script, tmp_path, fakes):
    from pathlib import Path

    # A beat stock sites can't match must NOT borrow an off-topic clip — it gets a GENERATED image
    # used as that shot, while the matchable beat still uses a real clip.
    scene = good_script.scenes[0]
    scene.b_roll_keywords = ["handshake across a desk", "the abstract dread of impostor syndrome"]
    one = good_script.model_copy(update={"scenes": [scene]})
    vo = VoiceoverAsset(
        run_id="0001",
        audio_path="assets/narration.mp3",
        duration_sec=6.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[SceneTiming(scene_index=scene.index, start=0.0, end=6.0)],
        provenance=Provenance(produced_by="voiceover"),
    )

    class _PartialBroll:
        enabled = True

        def __init__(self):
            self.downloaded: list[str] = []

        def search(self, query, *, context="", moment=""):
            # Only the concrete "handshake" beat matches; the abstract beat returns nothing.
            hit = "https://videos.pexels.com/video-files/handshake.mp4"
            return [hit] if "handshake" in query else []

        def download(self, url):
            self.downloaded.append(url)
            return b"FAKEVIDEO"

    image = fakes.Image()
    broll = _PartialBroll()
    pkg = Visuals(settings, image_provider=image, broll_client=broll).run(
        "0001", one, vo, run_root=tmp_path
    )
    sv = pkg.scenes[0]
    assert sv.kind == "broll"
    assert len(sv.shots) == 2
    by_suffix = {Path(s.path).suffix: s for s in sv.shots}
    assert set(by_suffix) == {".mp4", ".png"}  # one real clip + one generated image
    assert by_suffix[".png"].source == "fake-image"  # the gap beat was GENERATED, not borrowed
    assert by_suffix[".mp4"].source == "pexels"  # the matchable beat still used a real clip
    assert broll.downloaded == ["https://videos.pexels.com/video-files/handshake.mp4"]
    for shot in sv.shots:
        assert (tmp_path / shot.path).exists()


def test_visuals_keeps_existing_images_unless_asked_to_redo(
    settings, good_script, tmp_path, fakes, monkeypatch
):
    """Re-running visuals is usually about the FOOTAGE. Regenerating every image costs an API call
    each and silently discards anything replaced by hand, so an image already on disk is kept."""
    from pathlib import Path

    from content_foundry.config import get_settings, reset_settings_cache

    scene = good_script.scenes[0]
    scene.b_roll_keywords = ["the abstract dread of impostor syndrome"]
    one = good_script.model_copy(update={"scenes": [scene]})
    vo = VoiceoverAsset(
        run_id="0001",
        audio_path="assets/narration.mp3",
        duration_sec=6.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[SceneTiming(scene_index=scene.index, start=0.0, end=6.0)],
        provenance=Provenance(produced_by="voiceover"),
    )

    class _NoBroll:
        enabled = True

        def search(self, query, *, context="", moment=""):
            return []

        def download(self, url):  # pragma: no cover - never reached
            raise AssertionError("no clip should be downloaded")

    # First pass generates the image.
    image = fakes.Image()
    Visuals(settings, image_provider=image, broll_client=_NoBroll()).run(
        "0001", one, vo, run_root=tmp_path
    )
    shot_png = tmp_path / f"assets/scenes/scene_{scene.index}_shot_0.png"
    assert shot_png.exists()
    shot_png.write_bytes(b"HAND-MADE REPLACEMENT")  # stand in for an operator's own image
    after_first = image.calls

    # Second pass with redo OFF: the file is left exactly as-is and no new image is generated.
    monkeypatch.setenv("VISUALS_REDO_IMAGES", "false")
    reset_settings_cache()
    again = fakes.Image()
    pkg = Visuals(get_settings(), image_provider=again, broll_client=_NoBroll()).run(
        "0001", one, vo, run_root=tmp_path
    )
    assert shot_png.read_bytes() == b"HAND-MADE REPLACEMENT"  # untouched
    shot = next(s for s in pkg.scenes[0].shots if Path(s.path).suffix == ".png")
    assert shot.source == "reused"
    assert after_first > 0  # the first pass really did generate

    # Third pass with redo ON: the image is remade.
    monkeypatch.setenv("VISUALS_REDO_IMAGES", "true")
    reset_settings_cache()
    forced = fakes.Image()
    Visuals(get_settings(), image_provider=forced, broll_client=_NoBroll()).run(
        "0001", one, vo, run_root=tmp_path
    )
    assert shot_png.read_bytes() != b"HAND-MADE REPLACEMENT"  # regenerated on request


def test_broll_search_is_given_the_words_the_shot_will_sit_under(
    settings, good_script, tmp_path, fakes
):
    """The stock search used to receive only the scene-level beat. A scene runs 45-90 s across
    several claims, so a clip that matched the beat then landed under whichever claim happened to be
    playing — measured on run 0021, 'developer working on laptop coffee shop night' was showing under
    a line about inference latency. The provider's relevance gate cannot reject that without knowing
    the line, so each beat must search with its own slice of the narration."""
    seen: list[str] = []

    class _Recorder:
        enabled = True

        def search(self, query, *, context="", moment=""):
            seen.append(moment)
            return []

        def download(self, url):  # pragma: no cover - nothing is returned to download
            raise AssertionError("no clip should be downloaded")

    scene = good_script.scenes[0]
    scene.narration = "First half of the claim. " + "Second half says something else entirely."
    scene.b_roll_keywords = ["a beat", "another beat"]
    one = good_script.model_copy(update={"scenes": [scene]})
    vo = VoiceoverAsset(
        run_id="0001",
        audio_path="assets/narration.mp3",
        duration_sec=20.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[SceneTiming(scene_index=scene.index, start=0.0, end=20.0)],
        provenance=Provenance(produced_by="voiceover"),
    )
    Visuals(settings, image_provider=fakes.Image(), broll_client=_Recorder()).run(
        "0001", one, vo, run_root=tmp_path
    )
    assert len(seen) == 2, "each beat searches with its own slice of the narration"
    assert seen[0] != seen[1]
    assert "First half" in seen[0]
    assert "entirely" in seen[1]


def test_visuals_split_long_scene_into_ordered_beat_clips(settings, good_script, tmp_path, fakes):
    # A longer scene with several ordered keywords -> one B-roll clip per beat (moment-matched),
    # instead of a single broad clip for the whole scene.
    scene = good_script.scenes[0]
    scene.b_roll_keywords = [
        "handshake across a desk",
        "reading a job offer letter",
        "typing on a laptop",
    ]
    one = good_script.model_copy(update={"scenes": [scene]})
    vo = VoiceoverAsset(
        run_id="0001",
        audio_path="assets/narration.mp3",
        duration_sec=6.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[SceneTiming(scene_index=scene.index, start=0.0, end=6.0)],
        provenance=Provenance(produced_by="voiceover"),
    )
    broll = fakes.Broll()  # 10 distinct clips
    pkg = Visuals(settings, image_provider=None, broll_client=broll).run(
        "0001", one, vo, run_root=tmp_path
    )
    sv = pkg.scenes[0]
    assert sv.kind == "broll"
    assert len(sv.shots) == 3  # 6s / 2s-min = 3 beats, and 3 keywords supplied
    assert len(broll.downloaded) == 3  # a distinct clip pulled per beat
    assert [s.query for s in sv.shots] == scene.b_roll_keywords  # each beat -> its own search
    for shot in sv.shots:
        assert (tmp_path / shot.path).exists()
        assert abs(shot.duration_sec - 2.0) < 0.01  # the 6s scene split evenly across 3 beats


def test_thumbnail_text_decoupled_from_title(settings, good_script, tmp_path):
    vo = VoiceoverAsset(
        run_id="R",
        audio_path="assets/narration.mp3",
        duration_sec=6.0,
        sample_rate=16000,
        voice_id="v",
        provider="fake",
        word_timings=[],
        scene_timings=[
            SceneTiming(scene_index=s.index, start=0.0, end=3.0) for s in good_script.scenes
        ],
        provenance=Provenance(produced_by="voiceover"),
    )
    # A dedicated thumbnail_text wins over the title (they're independent now).
    s1 = good_script.model_copy(
        update={
            "title_options": ["How Recommendation Engines Work"],
            "thumbnail_text": "THEY'RE WATCHING YOU",
            "time_sensitive": False,
        }
    )
    pkg = Visuals(settings, image_provider=None, broll_client=None).run(
        "R", s1, vo, run_root=tmp_path
    )
    assert pkg.thumbnail_text == "THEY'RE WATCHING YOU"  # decoupled, not the title
    # An empty thumbnail_text falls back to a SHORT punchy version of the title (never the whole
    # long title, which is unreadable overlaid on a thumbnail).
    s2 = good_script.model_copy(
        update={
            "title_options": ["How to Get Into FAANG in 2026 (From a FAANG AI Scientist)"],
            "thumbnail_text": "",
            "time_sensitive": False,
        }
    )
    pkg2 = Visuals(settings, image_provider=None, broll_client=None).run(
        "R", s2, vo, run_root=tmp_path
    )
    assert pkg2.thumbnail_text == "Get Into FAANG in 2026"  # shortened fallback, not the full title


def test_fallback_thumb_text_shortens_and_handles_empty():
    from content_foundry.agents.visuals import _fallback_thumb_text

    assert (
        _fallback_thumb_text("How to Actually Get Into FAANG in 2026 (From a FAANG AI Scientist)")
        == "Actually Get Into FAANG in 2026"
    )
    assert _fallback_thumb_text("") == ""
