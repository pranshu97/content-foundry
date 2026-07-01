"""TTS provider protocol + ElevenLabs/OpenAI adapters (Ch. 10.5). SDKs imported lazily."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tenacity import retry, stop_after_attempt, wait_exponential

from ..errors import TTSError
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


class EdgeTTS:
    """Microsoft Edge neural TTS — free, high quality, needs internet. Gives real word timings.

    Install: ``pip install edge-tts``. No API key. Voices e.g. ``en-US-AriaNeural`` (F),
    ``en-US-GuyNeural`` (M); list them with ``edge-tts --list-voices``.
    """

    name = "edge"

    def __init__(self, voice: str = "en-US-AriaNeural", *, rate: str = "+0%", pitch: str = "+0Hz"):
        self._voice = voice or "en-US-AriaNeural"
        self._rate = rate
        self._pitch = pitch
        self.sample_rate = 24000

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def synthesize(self, text: str) -> tuple[bytes, list[WordTiming] | None]:
        import asyncio

        return asyncio.run(self._synth(text))

    async def _synth(self, text: str) -> tuple[bytes, list[WordTiming] | None]:
        try:
            import edge_tts  # lazy
        except ImportError as exc:
            raise TTSError("edge-tts is not installed. Run `pip install edge-tts`.") from exc

        comm = edge_tts.Communicate(text, self._voice, rate=self._rate, pitch=self._pitch)
        audio = bytearray()
        timings: list[WordTiming] = []
        try:
            async for chunk in comm.stream():
                ctype = chunk.get("type")
                if ctype == "audio":
                    audio += chunk["data"]
                elif ctype in ("WordBoundary", "SentenceBoundary"):
                    # edge-tts 7.x emits sentence-level boundaries; expand to per-word timings.
                    timings.extend(_split_boundary(chunk))
        except Exception as exc:  # network / voice errors
            raise TTSError(f"Edge TTS failed (voice={self._voice!r}): {exc}") from exc
        if not audio:
            raise TTSError(f"Edge TTS returned no audio (voice={self._voice!r}).")
        return bytes(audio), (timings or None)


def _split_boundary(chunk: dict) -> list[WordTiming]:
    """Turn an edge-tts (word/sentence) boundary into evenly-spaced per-word timings."""
    start = chunk.get("offset", 0) / 1e7  # 100ns ticks -> seconds
    dur = max(0.0, chunk.get("duration", 0) / 1e7)
    words = (chunk.get("text") or "").split()
    if len(words) <= 1:
        return [WordTiming(word=chunk.get("text") or "", start=start, end=start + dur)]
    step = dur / len(words)
    return [
        WordTiming(word=w, start=start + i * step, end=start + (i + 1) * step)
        for i, w in enumerate(words)
    ]


class PiperTTS:
    """Piper — fully offline neural TTS. Free, runs locally; needs a downloaded ``.onnx`` voice.

    Install: ``pip install piper-tts`` and download a voice (``.onnx`` + ``.onnx.json``) from
    https://huggingface.co/rhasspy/piper-voices . Outputs WAV, transcoded to mp3 via ffmpeg.
    """

    name = "piper"

    def __init__(self, model_path: str, executable: str = "piper") -> None:
        self._model_path = model_path
        self._exe = executable or "piper"
        self.sample_rate = 22050

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, max=4), reraise=True)
    def synthesize(self, text: str) -> tuple[bytes, list[WordTiming] | None]:
        wav = self._piper_wav(text)
        duration, rate = _wav_duration(wav)
        if rate:
            self.sample_rate = rate
        mp3 = _wav_to_mp3(wav)
        return mp3, (_even_word_timings(text, duration) or None)

    def _piper_wav(self, text: str) -> bytes:
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        if not self._model_path or not Path(self._model_path).exists():
            raise TTSError(
                f"Piper voice model not found at PIPER_MODEL_PATH={self._model_path!r}. "
                "Download a .onnx voice from https://huggingface.co/rhasspy/piper-voices"
            )
        if shutil.which(self._exe) is None:
            raise TTSError(f"Piper executable {self._exe!r} not on PATH. Run `pip install piper-tts`.")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.wav"
            proc = subprocess.run(
                [self._exe, "-m", self._model_path, "-f", str(out)],
                input=text.encode("utf-8"), capture_output=True, check=False,
            )
            if proc.returncode != 0 or not out.exists():
                raise TTSError(f"Piper synthesis failed: {proc.stderr.decode('utf-8', 'ignore')[:200]}")
            return out.read_bytes()


def _wav_duration(wav_bytes: bytes) -> tuple[float, int]:
    import io
    import wave

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            rate = wf.getframerate() or 22050
            return wf.getnframes() / float(rate), rate
    except Exception:
        return 0.0, 0


def _wav_to_mp3(wav_bytes: bytes) -> bytes:
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        raise TTSError("ffmpeg is required to encode Piper audio to mp3; install ffmpeg (see README).")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1"],
        input=wav_bytes, capture_output=True, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise TTSError(f"ffmpeg wav->mp3 failed: {proc.stderr.decode('utf-8', 'ignore')[:200]}")
    return proc.stdout


def _even_word_timings(text: str, duration: float) -> list[WordTiming]:
    words = text.split()
    if not words or duration <= 0:
        return []
    step = duration / len(words)
    return [WordTiming(word=w, start=i * step, end=(i + 1) * step) for i, w in enumerate(words)]
