"""Agent 4 — Voiceover / TTS. Narration + word/scene timings, the timing backbone (Ch. 10)."""

from __future__ import annotations

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
        audio = bytearray()
        word_timings: list[WordTiming] = []
        scene_timings: list[SceneTiming] = []
        cursor = 0.0

        for scene in sorted(script.scenes, key=lambda s: s.index):
            chunk, timings = self._tts.synthesize(scene.narration)
            audio += chunk
            if timings:
                duration = max((t.end for t in timings), default=_estimate(scene.narration))
                word_timings.extend(
                    WordTiming(word=t.word, start=cursor + t.start, end=cursor + t.end)
                    for t in timings
                )
            else:
                duration = _estimate(scene.narration)
                word_timings.extend(
                    _even_split(scene.narration.split(), cursor, cursor + duration)
                )
            scene_timings.append(
                SceneTiming(scene_index=scene.index, start=cursor, end=cursor + duration)
            )
            cursor += duration

        audio_path = run_root / _AUDIO_REL
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(bytes(audio))

        return VoiceoverAsset(
            run_id=run_id,
            audio_path=_AUDIO_REL,
            duration_sec=round(cursor, 3),
            sample_rate=getattr(self._tts, "sample_rate", 44100),
            voice_id=self._settings.tts_voice_id,
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
