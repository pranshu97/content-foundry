"""TTS provider protocol + ElevenLabs/OpenAI adapters (Ch. 10.5). SDKs imported lazily."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import WordTiming


@runtime_checkable
class TTSProvider(Protocol):
    name: str
    sample_rate: int

    def synthesize(self, text: str) -> tuple[bytes, list[WordTiming] | None]:
        """Return ``(audio_bytes, word_timings_or_None)``. None ⇒ caller must align."""
        ...


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self, api_key: str, voice_id: str, model: str, audio_format: str) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model = model
        self._format = audio_format
        self.sample_rate = 44100

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def synthesize(self, text: str) -> tuple[bytes, list[WordTiming] | None]:
        from elevenlabs.client import ElevenLabs  # lazy

        client = ElevenLabs(api_key=self._api_key)
        result = client.text_to_speech.convert_with_timestamps(
            voice_id=self._voice_id,
            model_id=self._model,
            output_format=self._format,
            text=text,
        )
        audio = result.audio_base64 if hasattr(result, "audio_base64") else result["audio_base64"]
        import base64

        audio_bytes = base64.b64decode(audio)
        timings = _parse_elevenlabs_timestamps(result)
        return audio_bytes, timings


def _parse_elevenlabs_timestamps(result: object) -> list[WordTiming] | None:
    alignment = getattr(result, "alignment", None)
    if not alignment:
        return None
    chars = getattr(alignment, "characters", None)
    starts = getattr(alignment, "character_start_times_seconds", None)
    ends = getattr(alignment, "character_end_times_seconds", None)
    if not (chars and starts and ends):
        return None
    # Collapse character timings into word timings.
    timings: list[WordTiming] = []
    word, w_start, w_end = "", None, 0.0
    for ch, s, e in zip(chars, starts, ends, strict=False):
        if ch.isspace():
            if word:
                timings.append(WordTiming(word=word, start=w_start or 0.0, end=w_end))
                word, w_start = "", None
        else:
            if w_start is None:
                w_start = s
            word += ch
            w_end = e
    if word:
        timings.append(WordTiming(word=word, start=w_start or 0.0, end=w_end))
    return timings or None


class OpenAITTS:
    name = "openai"

    def __init__(self, api_key: str, voice_id: str, model: str = "tts-1") -> None:
        self._api_key = api_key
        self._voice = voice_id
        self._model = model
        self.sample_rate = 24000

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def synthesize(self, text: str) -> tuple[bytes, list[WordTiming] | None]:
        import openai  # lazy

        client = openai.OpenAI(api_key=self._api_key)
        resp = client.audio.speech.create(model=self._model, voice=self._voice, input=text)
        # OpenAI TTS has no word timings -> alignment fallback handles it.
        return resp.read(), None
