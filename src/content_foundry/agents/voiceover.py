"""Agent 4 — Voiceover / TTS. Narration + word/scene timings, the timing backbone (Ch. 10)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, SceneTiming, Script, VoiceoverAsset, WordTiming

_WORDS_PER_SEC = 2.5
_AUDIO_REL = "assets/narration.mp3"


class Voiceover:
    def __init__(self, settings, tts_provider):
        self._settings = settings
        self._tts = tts_provider
        self._log = get_logger(component="voiceover")

    def run(self, run_id: str, script: Script, *, run_root: Path) -> VoiceoverAsset:
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

        # 2) Lock timings to the MEASURED audio. Byte-concatenating separate MP3s leaves encoder
        # delay/padding between them, so the track plays LONGER than the summed estimates and the
        # visuals drift AHEAD of the voice -- scenes cut mid-sentence, worse every scene. Decoding +
        # re-encoding once removes the gaps and gives exact per-scene lengths. Falls back to the old
        # estimate + byte-concat path when the audio can't be decoded (test fakes / no pydub+ffmpeg).
        decoded = _decode_concat(chunks)
        if decoded is not None:
            durations, audio_bytes = decoded
        else:
            durations, audio_bytes = estimates, b"".join(chunks)

        word_timings: list[WordTiming] = []
        scene_timings: list[SceneTiming] = []
        cursor = 0.0
        for scene, timings, est, dur in zip(
            scenes, provider_timings, estimates, durations, strict=True
        ):
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
                SceneTiming(scene_index=scene.index, start=cursor, end=cursor + dur)
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


def _decode_concat(chunks: list[bytes]) -> tuple[list[float], bytes] | None:
    """Decode each MP3 chunk to its TRUE length and re-encode ONE gapless track, returning
    ``(per-scene seconds, mp3 bytes)``. This is what keeps the visuals locked to the voice: byte-
    concatenating separate MP3s leaves encoder delay/padding between them, so the audio plays longer
    than the summed estimates and scenes cut mid-sentence. Returns ``None`` when pydub/ffmpeg is
    unavailable or any chunk is undecodable (e.g. the test fakes), so the caller falls back to
    estimates + raw byte concatenation (the original behavior)."""
    try:
        from pydub import AudioSegment  # lazy: needs pydub + ffmpeg
    except Exception:  # pragma: no cover - pydub is present in real installs
        return None
    combined = None
    durations: list[float] = []
    for chunk in chunks:
        try:
            seg = AudioSegment.from_file(BytesIO(chunk))
        except Exception:
            return None  # undecodable (e.g. the 32-null-byte fake) -> estimates + byte concat
        durations.append(len(seg) / 1000.0)  # pragma: no cover - real audio only
        combined = seg if combined is None else combined + seg  # pragma: no cover
    if combined is None or not durations:  # pragma: no cover - real audio only
        return None
    buf = BytesIO()  # pragma: no cover - real audio only
    combined.export(buf, format="mp3")  # pragma: no cover - needs ffmpeg
    return durations, buf.getvalue()  # pragma: no cover
