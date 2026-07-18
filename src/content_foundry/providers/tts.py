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


def pick_voice(run_id: str | None, *, male: str, female: str, default: str) -> str:
    """Alternate the narrator by run-id parity — male for ODD numeric ids, female for EVEN — so
    consecutive videos don't sound identical. Falls back to ``default`` when the male/female voices
    aren't configured or the run id isn't a plain number (e.g. a legacy ULID)."""
    if run_id is not None and str(run_id).isdigit() and (male or female):
        return (male if int(run_id) % 2 == 1 else female) or default
    return default


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self, api_key: str, voice_id: str, model: str, audio_format: str) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self.voice = voice_id  # public: the actual voice used (for reporting)
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
        self.voice = voice_id
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
        self.voice = self._voice
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
        self.voice = ""  # model-based; no named voice
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


def _ensure_perth_watermarker() -> None:
    """Chatterbox watermarks its output with perth's ``PerthImplicitWatermarker``, whose internal
    import needs ``pkg_resources`` (dropped from setuptools >= 81). On a modern env perth swallows that
    ImportError and leaves the class as ``None`` -> Chatterbox crashes with 'NoneType is not callable'.
    Install a NO-OP watermarker when that happens so voiceover still works (the audio is simply not
    perceptually watermarked); a warning is logged once."""
    try:
        import perth
    except Exception:
        return
    if getattr(perth, "PerthImplicitWatermarker", None) is not None:
        return  # perth is healthy; keep the real watermarker

    from ..logging import get_logger

    get_logger(component="tts").warning(
        "perth_watermarker_unavailable",
        detail="perth's watermarker failed to import (pkg_resources / setuptools >= 81); "
        'cloning proceeds WITHOUT the audio watermark. `pip install "setuptools<81"` to restore it.',
    )

    class _NoopWatermarker:
        def apply_watermark(self, wav, *args, **kwargs):
            return wav

        def get_watermark(self, *args, **kwargs):
            return None

    perth.PerthImplicitWatermarker = _NoopWatermarker


class ChatterboxTTS:
    """Chatterbox (Resemble AI) — FREE, offline, zero-shot voice cloning under the MIT license, so it's
    safe for a monetized channel. Clones from a SINGLE short (~15-30s) clean reference clip of your
    voice; runs locally (a CUDA GPU is strongly recommended). No native word timings -> even splits, so
    burned captions would drift; leave CAPTIONS_ENABLED off and let YouTube auto-CC caption the audio.
    The model loads once, then is reused.

    Install: ``pip install chatterbox-tts`` (pulls torch). Point TTS_REFERENCE_CLIP at your WAV.
    """

    name = "chatterbox"

    def __init__(self, reference_clip: str, *, device: str = "auto",
                 exaggeration: float = 0.5, cfg_weight: float = 0.5,
                 silence_pad_ms: int = 150) -> None:
        from pathlib import Path

        self._reference = reference_clip or ""
        self._device = device or "auto"
        self._exaggeration = exaggeration
        self._cfg_weight = cfg_weight
        self._silence_pad_ms = silence_pad_ms
        self.voice = Path(self._reference).stem if self._reference else "cloned"
        self.sample_rate = 24000
        self._model = None

    def _resolve_device(self) -> str:
        dev = (self._device or "auto").lower()
        if dev == "cpu":
            return "cpu"
        # cuda (explicit) OR auto -> use the NVIDIA GPU. If the operator DEMANDED cuda but torch can't
        # see it (almost always a CPU-only torch build), fail LOUDLY with the fix instead of silently
        # crawling on the CPU.
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        if dev == "cuda":
            raise TTSError(
                "TTS_CLONE_DEVICE=cuda but torch cannot see a CUDA GPU — you almost certainly have a "
                "CPU-only torch build (`torch==...+cpu`). Install the CUDA build, e.g.: "
                "pip install --index-url https://download.pytorch.org/whl/cu124 torch torchaudio"
            )
        return "cpu"  # auto + no GPU -> CPU (slow) so non-GPU users still work

    def _load(self):
        if self._model is None:
            try:
                from chatterbox.tts import ChatterboxTTS as _Chatterbox  # lazy
            except ImportError as exc:
                raise TTSError(
                    "chatterbox-tts is not installed. Run `pip install chatterbox-tts` "
                    "(installs torch; a CUDA GPU is strongly recommended)."
                ) from exc
            _ensure_perth_watermarker()
            device = self._resolve_device()
            from ..logging import get_logger

            get_logger(component="tts").info("chatterbox_loading", device=device)
            self._model = _Chatterbox.from_pretrained(device=device)
            self.sample_rate = int(getattr(self._model, "sr", 24000))
        return self._model

    def synthesize(self, text: str) -> tuple[bytes, list[WordTiming] | None]:
        from pathlib import Path

        from .text_normalize import speechify_numbers

        if not self._reference or not Path(self._reference).exists():
            raise TTSError(
                f"Cloning reference clip not found at TTS_REFERENCE_CLIP={self._reference!r}. "
                "Record a short (~15-30s) clean WAV of your voice and point this setting at it."
            )
        model = self._load()
        # Expand numbers/currency to words so the voice says "two hundred two thousand", not a
        # mangled "202,000" — Chatterbox's front-end mis-reads comma-grouped figures. Only the AUDIO
        # input is normalized; the original digits stay in the script for captions/citations.
        spoken = speechify_numbers(text)
        # Chatterbox generates at most ~1000 tokens (~40s) PER CALL, so a long scene voiced in one
        # shot is TRUNCATED mid-sentence and the video then cuts to the next scene before the line
        # finishes. Split into sentence-sized chunks that each sit well inside that window, then
        # stitch the audio back into one continuous scene.
        import torch

        pieces = []
        for chunk in _chunk_for_tts(spoken):
            try:
                wav = model.generate(
                    chunk, audio_prompt_path=self._reference,
                    exaggeration=self._exaggeration, cfg_weight=self._cfg_weight,
                )
            except Exception as exc:
                raise TTSError(f"Chatterbox synthesis failed: {exc}") from exc
            if hasattr(wav, "dim") and wav.dim() == 1:
                wav = wav.unsqueeze(0)
            # Trim Chatterbox's leading/trailing silence per chunk so stitched sentences/scenes don't
            # pile up long dead-air pauses (a small pad keeps a natural beat between sentences).
            pieces.append(_trim_silence(wav, self.sample_rate, pad_ms=self._silence_pad_ms))
        if not pieces:
            raise TTSError("Chatterbox produced no audio (the scene narration was empty).")
        wav = torch.cat(pieces, dim=-1) if len(pieces) > 1 else pieces[0]
        # Duration straight from the tensor (samples / sr) — robust vs the stdlib wave reader, which
        # can't parse torchaudio's float WAV (it would yield 0 and silently drop the word timings).
        duration = float(wav.shape[-1]) / float(self.sample_rate or 24000)
        wav_bytes = _tensor_to_wav_bytes(wav, self.sample_rate)
        return _wav_to_mp3(wav_bytes), (_even_word_timings(text, duration) or None)


def _tensor_to_wav_bytes(wav, sample_rate: int) -> bytes:
    """Serialize a torch audio tensor (channels, samples) to in-memory WAV bytes."""
    import io

    import torchaudio

    buf = io.BytesIO()
    torchaudio.save(buf, wav, sample_rate, format="wav")
    return buf.getvalue()


def _trim_silence(wav, sample_rate: int, *, thresh: float = 0.015, pad_ms: int = 40):  # pragma: no cover - needs torch + real audio
    """Trim leading/trailing near-silence from a Chatterbox waveform tensor (channels, samples),
    keeping a small pad, so stitched chunks/scenes don't accumulate dead air. Returns the tensor
    unchanged when it is all silence or the shape is unexpected."""
    try:
        import torch

        amp = wav.abs()
        if amp.dim() == 2:
            amp = amp.mean(dim=0)
        nz = torch.nonzero(amp > thresh).flatten()
        if nz.numel() == 0:
            return wav
        pad = int(sample_rate * pad_ms / 1000)
        start = max(0, int(nz[0]) - pad)
        end = min(int(amp.shape[-1]), int(nz[-1]) + pad + 1)
        return wav[..., start:end]
    except Exception:
        return wav


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


def _chunk_for_tts(text: str, max_chars: int = 300) -> list[str]:
    """Split narration into sentence-grouped chunks that each fit inside a neural TTS model's
    per-call generation window. Chatterbox caps a single ``generate`` at ~1000 tokens (~40s) and
    silently truncates anything longer, so a ~150-word scene must be voiced in pieces and stitched.
    Sentences are kept whole where they fit; a lone over-long sentence is split on word boundaries."""
    import re

    text = " ".join(text.split())
    if not text:
        return []
    chunks: list[str] = []
    cur = ""
    for raw in re.findall(r"[^.!?]+[.!?]*", text):
        sentence = raw.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            word_run = ""
            for w in sentence.split():
                if word_run and len(word_run) + len(w) + 1 > max_chars:
                    chunks.append(word_run)
                    word_run = w
                else:
                    word_run = f"{word_run} {w}".strip()
            cur = word_run
        elif cur and len(cur) + len(sentence) + 1 > max_chars:
            chunks.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur:
        chunks.append(cur)
    return chunks
