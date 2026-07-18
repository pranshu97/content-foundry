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
    words = [WordTiming(word=w, start=float(i), end=float(i) + 0.4)
             for i, w in enumerate(script.hook.split())]
    return VoiceoverAsset(
        run_id="R", audio_path="assets/narration.mp3", duration_sec=float(len(script.scenes) * 3),
        sample_rate=16000, voice_id="v", provider="fake",
        word_timings=words, scene_timings=scene_timings, provenance=Provenance(produced_by="voiceover"),
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


def test_faceid_prompt_leads_with_person_and_stays_under_clip_limit():
    from content_foundry.agents.visuals import _faceid_prompt

    # The FaceID model GENERATES the operator's face, so the person leads (prominent for identity) but
    # the concept sets the SCENE so the thumbnail matches the content. Stays under CLIP's 77 tokens.
    p = _faceid_prompt("a shocked software engineer reacting to a laptop full of code")
    assert p.startswith("high-CTR YouTube thumbnail")
    assert "a shocked software engineer" in p  # the LLM's concept drives the scene (dynamic per video)
    assert "one prominent person" in p  # the operator's face is the subject
    assert len(p.split()) < 60
    # A pathologically long concept is trimmed so the prompt still fits inside CLIP's window.
    assert len(_faceid_prompt("word " * 80).split()) < 60


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
        good_script, run_root=tmp_path, prompt="EXPLICIT OVERRIDE")
    assert img3.last_prompt == "EXPLICIT OVERRIDE"
    assert prompt_file.read_text(encoding="utf-8") == "EXPLICIT OVERRIDE"


def test_faceid_thumbnail_degrades_to_none_without_the_heavy_stack(settings, tmp_path):
    from content_foundry.providers.faceid import generate_face_image

    # The heavy stack (diffusers/insightface) isn't installed in tests and a fake image can't decode,
    # so face-id generation must return None -> the caller falls back to the composited thumbnail.
    face = tmp_path / "face.png"
    face.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert generate_face_image(
        settings, prompt="a shocked engineer", face_path=str(face), size="1280x720"
    ) is None
    # A missing face path also returns None (pre-check, before any heavy import).
    assert generate_face_image(
        settings, prompt="x", face_path=str(tmp_path / "nope.png"), size="1280x720"
    ) is None


def test_faceswap_degrades_to_none(settings, tmp_path):
    from content_foundry.providers.faceswap import swap_face

    # Two-stage face-swap: an undecodable scene or a missing avatar returns None (before any heavy
    # model load), so the caller falls back to the composited thumbnail and the render never breaks.
    face = tmp_path / "face.png"
    face.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert swap_face(settings, scene_png=b"\x89PNG\r\n\x1a\n", face_path=str(face)) is None
    assert swap_face(settings, scene_png=b"", face_path=str(face)) is None  # empty scene -> pre-check
    assert swap_face(settings, scene_png=b"x", face_path=str(tmp_path / "nope.png")) is None


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


def test_visuals_split_long_scene_into_ordered_beat_clips(settings, good_script, tmp_path, fakes):
    # A longer scene with several ordered keywords -> one B-roll clip per beat (moment-matched),
    # instead of a single broad clip for the whole scene.
    scene = good_script.scenes[0]
    scene.b_roll_keywords = [
        "handshake across a desk", "reading a job offer letter", "typing on a laptop",
    ]
    one = good_script.model_copy(update={"scenes": [scene]})
    vo = VoiceoverAsset(
        run_id="0001", audio_path="assets/narration.mp3", duration_sec=6.0, sample_rate=16000,
        voice_id="v", provider="fake", word_timings=[],
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
        run_id="R", audio_path="assets/narration.mp3", duration_sec=6.0, sample_rate=16000,
        voice_id="v", provider="fake", word_timings=[],
        scene_timings=[SceneTiming(scene_index=s.index, start=0.0, end=3.0)
                       for s in good_script.scenes],
        provenance=Provenance(produced_by="voiceover"),
    )
    # A dedicated thumbnail_text wins over the title (they're independent now).
    s1 = good_script.model_copy(update={
        "title_options": ["How Recommendation Engines Work"],
        "thumbnail_text": "THEY'RE WATCHING YOU", "time_sensitive": False,
    })
    pkg = Visuals(settings, image_provider=None, broll_client=None).run("R", s1, vo, run_root=tmp_path)
    assert pkg.thumbnail_text == "THEY'RE WATCHING YOU"  # decoupled, not the title
    # An empty thumbnail_text falls back to a SHORT punchy version of the title (never the whole
    # long title, which is unreadable overlaid on a thumbnail).
    s2 = good_script.model_copy(update={
        "title_options": ["How to Get Into FAANG in 2026 (From a FAANG AI Scientist)"],
        "thumbnail_text": "", "time_sensitive": False,
    })
    pkg2 = Visuals(settings, image_provider=None, broll_client=None).run("R", s2, vo, run_root=tmp_path)
    assert pkg2.thumbnail_text == "Get Into FAANG in 2026"  # shortened fallback, not the full title


def test_fallback_thumb_text_shortens_and_handles_empty():
    from content_foundry.agents.visuals import _fallback_thumb_text

    assert _fallback_thumb_text(
        "How to Actually Get Into FAANG in 2026 (From a FAANG AI Scientist)"
    ) == "Actually Get Into FAANG in 2026"
    assert _fallback_thumb_text("") == ""
