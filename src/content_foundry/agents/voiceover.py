"""Agent 4 — Voiceover / TTS. Narration + word/scene timings, the timing backbone (Ch. 10)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, SceneTiming, Script, VoiceoverAsset, WordTiming

_WORDS_PER_SEC = 2.5
_AUDIO_REL = "assets/narration.mp3"

# Which delivery to clone for each script shape. The template already encodes the video's rhetorical
# job, so the tone comes free -- no extra model call. A contrarian piece lives on stress ("no, it is
# actually THIS"), a data deep-dive needs room around the numbers, a case study wants narrative
# momentum. Anything unmapped stays on the neutral baseline.
_TEMPLATE_TONES: dict[str, str] = {
    "contrarian": "punchy",
    "myth_vs_reality": "punchy",
    "data_deep_dive": "authoritative",
    "three_step": "authoritative",
    "problem_solution": "energetic",
    "case_study": "energetic",
}


def tone_for_script(template_id: str, *, override: str = "") -> str:
    """Delivery tone for a script: an explicit ``override`` wins, else it is derived from the
    template. ``override='auto'`` (or blank) means derive. Pure, so it is unit-tested directly."""
    choice = (override or "").strip().lower()
    if choice and choice != "auto":
        return choice
    return _TEMPLATE_TONES.get((template_id or "").strip().lower(), "neutral")


class Voiceover:
    def __init__(self, settings, tts_provider):
        self._settings = settings
        self._tts = tts_provider
        self._log = get_logger(component="voiceover")

    def run(self, run_id: str, script: Script, *, run_root: Path) -> VoiceoverAsset:
        # 0) Tell a cloning provider WHICH delivery to imitate before it prepares its reference.
        # Best-effort: providers without a tone (ElevenLabs/Edge/OpenAI/Piper) simply don't have the
        # hook, so this is a no-op for them.
        tone = tone_for_script(script.template_id, override=getattr(self._settings, "tts_tone", ""))
        setter = getattr(self._tts, "set_tone", None)
        if callable(setter):
            setter(tone)
            self._log.info("voice_tone_selected", tone=tone, template=script.template_id)

        # 1) Synthesize every scene up front, keeping each provider's raw audio + its timings/estimate.
        scenes = sorted(script.scenes, key=lambda s: s.index)
        chunks: list[bytes] = []
        provider_timings: list[list[WordTiming] | None] = []
        estimates: list[float] = []
        for scene in scenes:
            chunk, timings = self._tts.synthesize(scene.narration)
            chunks.append(chunk)
            provider_timings.append(timings)
            estimates.append(
                max((t.end for t in timings), default=_estimate(scene.narration))
                if timings
                else _estimate(scene.narration)
            )

        # 2) Reserve silence BEFORE a scene's narration so nothing has to play over the voice: an
        # opening beat so the video doesn't start abruptly on speech, and a clear beat for any scene
        # with a sound effect (effects are cued at the scene start, i.e. exactly on the first word,
        # so without this every effect talks over the narration). A scene needing both takes the
        # longer of the two rather than stacking them into a long dead opening.
        lead_in = max(0.0, float(self._settings.voiceover_lead_in_sec))
        sfx_gap = max(0.0, float(self._settings.sfx_gap_sec))
        gaps = [
            max(lead_in if i == 0 else 0.0, sfx_gap if scene.sfx else 0.0)
            for i, scene in enumerate(scenes)
        ]

        # 3) Lock timings to the MEASURED audio. Byte-concatenating separate MP3s leaves encoder
        # delay/padding between them, so the track plays LONGER than the summed estimates and the
        # visuals drift AHEAD of the voice -- scenes cut mid-sentence, worse every scene. Decoding +
        # re-encoding once removes the gaps and gives exact per-scene lengths. Falls back to the old
        # estimate + byte-concat path when the audio can't be decoded (test fakes / no pydub+ffmpeg).
        decoded = _decode_concat(chunks, gaps=gaps)
        if decoded is not None:
            durations, audio_bytes = decoded
        else:
            durations, audio_bytes = estimates, b"".join(chunks)
            # The fallback path can't pad the audio, so it must NOT shift the timings either — doing
            # so would slide every caption and visual off the voice by the reserved gaps.
            gaps = [0.0] * len(scenes)

        word_timings: list[WordTiming] = []
        scene_timings: list[SceneTiming] = []
        cursor = 0.0
        for scene, timings, est, dur, gap in zip(
            scenes, provider_timings, estimates, durations, gaps, strict=True
        ):
            # The scene OWNS its gap: its visual (and its sound effect, cued at this same start) begin
            # here, then the voice comes in once the silence has played out.
            scene_start = cursor
            cursor += gap
            if timings:
                # Rescale provider/even timings onto the real scene length so words stay aligned.
                scale = (dur / est) if est > 1e-6 else 1.0
                word_timings.extend(
                    WordTiming(
                        word=t.word, start=cursor + t.start * scale, end=cursor + t.end * scale
                    )
                    for t in timings
                )
            else:
                word_timings.extend(_even_split(scene.narration.split(), cursor, cursor + dur))
            scene_timings.append(
                SceneTiming(scene_index=scene.index, start=scene_start, end=cursor + dur)
            )
            cursor += dur

        audio_path = run_root / _AUDIO_REL
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(audio_bytes)

        return VoiceoverAsset(
            run_id=run_id,
            audio_path=_AUDIO_REL,
            duration_sec=round(cursor, 3),
            sample_rate=getattr(self._tts, "sample_rate", 44100),
            voice_id=getattr(self._tts, "voice", "") or self._settings.tts_voice_id,
            provider=getattr(self._tts, "name", self._settings.tts_provider),
            word_timings=word_timings,
            scene_timings=scene_timings,
            provenance=Provenance(
                produced_by="voiceover", model=None, config_hash=self._settings.config_hash
            ),
        )


def _estimate(text: str) -> float:
    return max(1.0, len(text.split()) / _WORDS_PER_SEC)


def _even_split(words: list[str], start: float, end: float) -> list[WordTiming]:
    if not words:
        return []
    step = (end - start) / len(words)
    return [
        WordTiming(word=w, start=start + i * step, end=start + (i + 1) * step)
        for i, w in enumerate(words)
    ]


def _decode_concat(
    chunks: list[bytes], *, gaps: list[float] | None = None
) -> tuple[list[float], bytes] | None:
    """Decode each MP3 chunk to its TRUE length and re-encode ONE gapless track, returning
    ``(per-scene seconds, mp3 bytes)``. This is what keeps the visuals locked to the voice: byte-
    concatenating separate MP3s leaves encoder delay/padding between them, so the audio plays longer
    than the summed estimates and scenes cut mid-sentence. ``gaps`` inserts that many seconds of
    silence BEFORE each chunk (the opening beat / room for a sound effect); the returned durations
    stay pure narration, so the caller stays the single owner of where the gaps land on the timeline.
    Returns ``None`` when pydub/ffmpeg is unavailable or any chunk is undecodable (e.g. the test
    fakes), so the caller falls back to estimates + raw byte concatenation (the original behavior)."""
    try:
        from pydub import AudioSegment  # lazy: needs pydub + ffmpeg
    except Exception:  # pragma: no cover - pydub is present in real installs
        return None
    pads = list(gaps or [])
    combined = None
    durations: list[float] = []
    for i, chunk in enumerate(chunks):
        try:
            seg = AudioSegment.from_file(BytesIO(chunk))
        except Exception:
            return None  # undecodable (e.g. the 32-null-byte fake) -> estimates + byte concat
        durations.append(len(seg) / 1000.0)  # pragma: no cover - real audio only
        gap = pads[i] if i < len(pads) else 0.0  # pragma: no cover - real audio only
        if gap > 0:  # pragma: no cover - real audio only
            seg = AudioSegment.silent(duration=int(gap * 1000)) + seg
        combined = seg if combined is None else combined + seg  # pragma: no cover
    if combined is None or not durations:  # pragma: no cover - real audio only
        return None
    buf = BytesIO()  # pragma: no cover - real audio only
    combined.export(buf, format="mp3")  # pragma: no cover - needs ffmpeg
    return durations, buf.getvalue()  # pragma: no cover
