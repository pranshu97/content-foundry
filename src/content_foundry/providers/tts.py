"""TTS provider protocol + ElevenLabs/OpenAI adapters (Ch. 10.5). SDKs imported lazily."""

from __future__ import annotations

import contextlib
import json
from typing import Any, Protocol, runtime_checkable

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


TONE_WEIGHTS: dict[str, dict[str, float]] = {
    # EXACTLY today's behaviour: simply the densest speech. Density-only on purpose -- this is the
    # safe baseline and the escape hatch, so it must not quietly drift from what was validated.
    "neutral": {"density": 1.0, "dynamics": 0.0, "pitch": 0.0, "pace": 0.0},
    # Deliberate and weighty: wide dynamics but SLOW, with room to breathe. For number-heavy or
    # step-by-step material where the listener has to keep up.
    "authoritative": {"density": -0.3, "dynamics": 1.0, "pitch": 0.2, "pace": -0.8},
    # Emphatic: the widest dynamics and the liveliest pitch, moderately quick. For arguing against
    # something, where the stress pattern carries the point.
    "punchy": {"density": 0.3, "dynamics": 1.0, "pitch": 0.8, "pace": 0.4},
    # Fast and full: dense and quick above all. Pitch stays LOW-weighted here or a merely expressive
    # window outscores the genuinely fast one, which is punchy's job, not this one's.
    "energetic": {"density": 0.9, "dynamics": 0.2, "pitch": 0.2, "pace": 1.0},
}
TONE_FEATURES = ("density", "dynamics", "pitch", "pace")
DEFAULT_TONE = "neutral"


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
            raise TTSError(
                f"Piper executable {self._exe!r} not on PATH. Run `pip install piper-tts`."
            )
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.wav"
            proc = subprocess.run(
                [self._exe, "-m", self._model_path, "-f", str(out)],
                input=text.encode("utf-8"),
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0 or not out.exists():
                raise TTSError(
                    f"Piper synthesis failed: {proc.stderr.decode('utf-8', 'ignore')[:200]}"
                )
            return out.read_bytes()


# Emotion vectors for IndexTTS-2, in ITS documented order:
# [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm].
# Deliberately LOW intensities. This model will happily sound theatrical, which on an explainer
# reads as a stunt; the aim is a lean on the delivery, not a performance. "neutral" carries no
# vector at all so it stays a pure clone.
TONE_EMOTIONS: dict[str, list[float]] = {
    "neutral": [],
    "authoritative": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.45],
    "punchy": [0.15, 0.30, 0.0, 0.0, 0.0, 0.0, 0.10, 0.0],
    "energetic": [0.40, 0.0, 0.0, 0.0, 0.0, 0.0, 0.15, 0.0],
}


class IndexTTS2:
    """IndexTTS-2 (Bilibili) driven out-of-process -- emotion is DISENTANGLED from timbre, so the
    voice stays yours while the delivery is steered separately.

    It cannot share this interpreter: IndexTTS-2 needs numpy>=2 and a newer transformers, while
    Chatterbox pins numpy<2 + transformers 4.44. So it lives in its own venv and is driven through
    ``indextts2_worker.py`` over a line protocol (the same out-of-process shape ``PiperTTS`` uses,
    but persistent -- loading the model takes tens of seconds and a video is dozens of chunks).

    Setup is in the README of https://github.com/index-tts/index-tts ; point ``INDEXTTS_PYTHON`` at
    that checkout's ``.venv`` interpreter and ``INDEXTTS_MODEL_DIR`` at its ``checkpoints``.
    """

    name = "indextts"

    def __init__(
        self,
        reference_clip: str,
        *,
        python_exe: str,
        model_dir: str,
        cfg_path: str = "",
        fp16: bool = True,
        precision: str = "fp16",
        emotion: str = "off",
        emo_alpha: float = 0.6,
        edge_pad_ms: int = 40,
        sentence_pause_ms: int = 300,
        silence_pad_ms: int = 150,
        max_pause_ms: int = 1000,
        reference_window_sec: float = 12.0,
        tone: str = DEFAULT_TONE,
    ) -> None:
        from pathlib import Path

        self._reference = reference_clip or ""
        self._python = python_exe or ""
        self._model_dir = model_dir or ""
        self._cfg = cfg_path or (str(Path(model_dir) / "config.yaml") if model_dir else "")
        self._fp16 = fp16
        self._precision = (precision or "fp16").strip().lower()
        self._emotion = (emotion or "off").strip().lower()
        self._emo_alpha = emo_alpha
        self._edge_pad_ms = edge_pad_ms
        self._sentence_pause_ms = sentence_pause_ms
        self._silence_pad_ms = silence_pad_ms
        self._max_pause_ms = max_pause_ms
        self._reference_window_sec = reference_window_sec
        self._prepared_reference = ""
        self._tone = tone or DEFAULT_TONE
        self.voice = Path(self._reference).stem if self._reference else "cloned"
        self.sample_rate = 22050  # corrected from the first synthesized file
        self._proc: Any = None
        self._stderr_path = ""

    def set_tone(self, tone: str) -> None:
        """Choose the delivery (see ``TONE_WEIGHTS``). Steers the emotion vector when emotion is on,
        and always decides WHICH window of the reference gets cloned. An unknown or blank tone is
        ignored so a caller can never break synthesis."""
        tone = (tone or "").strip().lower()
        if not tone or tone not in TONE_WEIGHTS or tone == self._tone:
            return
        self._tone = tone
        self._prepared_reference = ""

    def _conditioning_clip(self) -> str:
        """Path to the clip to clone from -- condensed, and NOT for the reason Chatterbox needs it.

        IndexTTS-2 re-derives the speaker conditioning (w2v-bert + speaker encoder) from this file on
        EVERY ``infer`` call, so the reference's LENGTH is paid once per chunk, dozens of times per
        video. MEASURED against the raw 102 s clip: ~166 s of fixed cost per call versus only ~1.5 s
        per additional word -- i.e. nearly all the runtime was re-encoding the same reference over and
        over, not generating speech. Condensing to the tone-matched window attacks that directly.
        Falls back to the original clip on any failure."""
        if self._prepared_reference:
            return self._prepared_reference
        self._prepared_reference = _prepare_reference(
            self._reference, window_sec=self._reference_window_sec, tone=self._tone
        )
        return self._prepared_reference

    def _emotion_vector(self) -> list[float]:
        if self._emotion != "auto":
            return []
        return list(TONE_EMOTIONS.get(self._tone, []))

    def _start(self):  # pragma: no cover - spawns the model process
        import subprocess
        import tempfile
        from pathlib import Path

        if self._proc is not None:
            return self._proc
        if not self._python or not Path(self._python).exists():
            raise TTSError(
                f"INDEXTTS_PYTHON={self._python!r} is not an interpreter. Clone "
                "https://github.com/index-tts/index-tts , run `uv sync --all-extras` there, and "
                "point this at that checkout's .venv/Scripts/python.exe"
            )
        if not self._cfg or not Path(self._cfg).exists():
            raise TTSError(
                f"IndexTTS-2 config not found at {self._cfg!r}. Download the weights with "
                "`hf download IndexTeam/IndexTTS-2 --local-dir=checkpoints` and set "
                "INDEXTTS_MODEL_DIR to that checkpoints directory."
            )
        worker = Path(__file__).with_name("indextts2_worker.py")
        cmd = [self._python, str(worker), "--cfg", self._cfg, "--model-dir", self._model_dir]
        cmd += ["--precision", self._precision]
        if self._fp16:
            cmd.append("--fp16")
        # stderr goes to a FILE, never a pipe: the model logs heavily while loading and an undrained
        # pipe would fill and deadlock the worker mid-load.
        self._stderr_path = str(Path(tempfile.gettempdir()) / "cf_indextts2.log")
        from ..logging import get_logger

        get_logger(component="tts").info(
            "indextts2_starting", model_dir=self._model_dir, fp16=self._fp16, log=self._stderr_path
        )
        proc = subprocess.Popen(  # noqa: S603 - operator-configured interpreter
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open(self._stderr_path, "w", encoding="utf-8"),  # noqa: SIM115
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(Path(self._model_dir).parent) if self._model_dir else None,
        )
        self._proc = proc
        ready = proc.stdout.readline() if proc.stdout else ""
        if not ready or not json.loads(ready or "{}").get("ok"):
            raise TTSError(f"IndexTTS-2 failed to start; see {self._stderr_path}")
        return proc

    def _request(self, job: dict) -> None:  # pragma: no cover - needs the model process
        proc = self._start()
        proc.stdin.write(json.dumps(job) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            raise TTSError(f"IndexTTS-2 worker died; see {self._stderr_path}")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise TTSError(f"IndexTTS-2 synthesis failed: {reply.get('error')}")

    def close(self) -> None:  # pragma: no cover - needs the model process
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            proc.stdin.write(json.dumps({"stop": True}) + "\n")
            proc.stdin.flush()
            proc.wait(timeout=20)
        with contextlib.suppress(Exception):
            proc.kill()

    def synthesize(self, text: str) -> tuple[bytes, list[WordTiming] | None]:  # pragma: no cover
        import tempfile
        from pathlib import Path

        import torch
        import torchaudio

        from .text_normalize import speechify_numbers

        if not self._reference or not Path(self._reference).exists():
            raise TTSError(
                f"Cloning reference clip not found at TTS_REFERENCE_CLIP={self._reference!r}."
            )
        spoken = speechify_numbers(text)
        vector = self._emotion_vector()
        speaker = str(Path(self._conditioning_clip()).resolve())
        pieces = []
        with tempfile.TemporaryDirectory() as td:
            chunks = _chunk_for_tts(spoken, max_chars=400)
            for i, chunk in enumerate(chunks):
                out = str(Path(td) / f"chunk_{i}.wav")
                job = {
                    "text": chunk,
                    "out": out,
                    "speaker": speaker,
                    "emo_alpha": self._emo_alpha,
                }
                if vector:
                    job["emo_vector"] = vector
                self._request(job)
                wav, rate = torchaudio.load(out)
                self.sample_rate = int(rate)
                # Same stitching as Chatterbox: trim the chunk edges close, then put a pause back at
                # the length the PUNCTUATION earns, so this provider inherits the fix rather than
                # re-growing a metronome of its own.
                piece = _trim_silence(
                    wav,
                    self.sample_rate,
                    pad_ms=self._silence_pad_ms,
                    max_pause_ms=self._max_pause_ms,
                    edge_pad_ms=self._edge_pad_ms,
                )
                pieces.append(piece)
                if i < len(chunks) - 1:
                    gap_ms = max(
                        0,
                        pause_after_ms(chunk, base=self._sentence_pause_ms) - 2 * self._edge_pad_ms,
                    )
                    gap = int(self.sample_rate * gap_ms / 1000)
                    if gap > 0:
                        pieces.append(piece.new_zeros((piece.shape[0], gap)))
            if not pieces:
                raise TTSError("IndexTTS-2 produced no audio (the scene narration was empty).")
            wav = torch.cat(pieces, dim=-1) if len(pieces) > 1 else pieces[0]
        duration = float(wav.shape[-1]) / float(self.sample_rate or 22050)
        mp3 = _wav_to_mp3(_tensor_to_wav_bytes(wav, self.sample_rate))
        return mp3, (_even_word_timings(text, duration) or None)


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


# A cloning reference is not one uniform performance: MEASURED across 46 twelve-second windows of the
# operator's own 102 s recording, pace varied by 46% (4.4 -> 7.0 onsets/sec) and dynamic range by 27%
# (7.2 -> 9.5 dB), while the pitch MEDIAN moved only 10%. So which window is handed to the cloner
# changes HOW the speaker delivers without changing WHO they sound like -- which makes the window a
# real tone control. ``TONE_WEIGHTS`` (top of this module) holds the per-tone directions.


class ChatterboxTTS:
    """Chatterbox (Resemble AI) — FREE, offline, zero-shot voice cloning under the MIT license, so it's
    safe for a monetized channel. Clones from a SINGLE short (~15-30s) clean reference clip of your
    voice; runs locally (a CUDA GPU is strongly recommended). No native word timings -> even splits, so
    burned captions would drift; leave CAPTIONS_ENABLED off and let YouTube auto-CC caption the audio.
    The model loads once, then is reused.

    Install: ``pip install chatterbox-tts`` (pulls torch). Point TTS_REFERENCE_CLIP at your WAV.
    """

    name = "chatterbox"

    def __init__(
        self,
        reference_clip: str,
        *,
        device: str = "auto",
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
        silence_pad_ms: int = 150,
        max_pause_ms: int = 1000,
        edge_pad_ms: int = 40,
        sentence_pause_ms: int = 300,
        reference_window_sec: float = 12.0,
        tone: str = DEFAULT_TONE,
    ) -> None:
        from pathlib import Path

        self._reference = reference_clip or ""
        self._device = device or "auto"
        self._exaggeration = exaggeration
        self._cfg_weight = cfg_weight
        self._silence_pad_ms = silence_pad_ms
        self._max_pause_ms = max_pause_ms
        self._edge_pad_ms = edge_pad_ms
        self._sentence_pause_ms = sentence_pause_ms
        self._reference_window_sec = reference_window_sec
        self._tone = tone or DEFAULT_TONE
        self._prepared_reference = ""
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

    def set_tone(self, tone: str) -> None:
        """Choose the DELIVERY to clone (see ``TONE_WEIGHTS``). Called best-effort by the voiceover
        agent once it knows what kind of video this is. Invalidates any window already prepared for a
        different tone; an unknown/blank tone is ignored so a caller can never break synthesis."""
        tone = (tone or "").strip().lower()
        if not tone or tone not in TONE_WEIGHTS or tone == self._tone:
            return
        self._tone = tone
        self._prepared_reference = ""

    def _conditioning_clip(self) -> str:
        """Path to the clip Chatterbox should actually clone from.

        Chatterbox uses only the FIRST ~6 s of the reference for prosody (``ENC_COND_LEN``) and the
        first ~10 s for timbre (``DEC_COND_LEN``), discarding everything after. So a long recording is
        judged entirely on its opening seconds: leading silence and dead air there are what flatten the
        delivery -- the model learns "this speaker pauses constantly" and little about how they stress
        a line. Condense the reference to the window whose delivery best matches this video's tone,
        ONCE per process per tone. Any failure falls back to the original clip, so this can only help
        or no-op."""
        if self._prepared_reference:
            return self._prepared_reference
        self._prepared_reference = _prepare_reference(
            self._reference, window_sec=self._reference_window_sec, tone=self._tone
        )
        return self._prepared_reference

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

        reference = self._conditioning_clip()
        chunks = _chunk_for_tts(spoken)
        pieces = []
        for i, chunk in enumerate(chunks):
            try:
                wav = model.generate(
                    chunk,
                    audio_prompt_path=reference,
                    exaggeration=self._exaggeration,
                    cfg_weight=self._cfg_weight,
                )
            except Exception as exc:
                raise TTSError(f"Chatterbox synthesis failed: {exc}") from exc
            if hasattr(wav, "dim") and wav.dim() == 1:
                wav = wav.unsqueeze(0)
            # Trim Chatterbox's leading/trailing silence per chunk so stitched sentences/scenes don't
            # pile up dead air, and collapse any very long INTERNAL pause the cloner emits between two
            # sentences in a chunk.
            piece = _trim_silence(
                wav,
                self.sample_rate,
                pad_ms=self._silence_pad_ms,
                max_pause_ms=self._max_pause_ms,
                edge_pad_ms=self._edge_pad_ms,
            )
            pieces.append(piece)
            # Then put the pause back at a length the PUNCTUATION earns. The edges above are trimmed
            # close precisely so this explicit gap -- not the trimmer's fixed pad -- is what the
            # listener hears, which is what stops every join sounding like the same metronome tick.
            if i < len(chunks) - 1:
                gap_ms = max(
                    0, pause_after_ms(chunk, base=self._sentence_pause_ms) - 2 * self._edge_pad_ms
                )
                gap = int(self.sample_rate * gap_ms / 1000)
                if gap > 0:
                    pieces.append(piece.new_zeros((piece.shape[0], gap)))
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


# How long a pause should be AFTER a chunk, as a multiple of the base sentence pause, chosen by the
# punctuation the chunk ends on. A chunk that ends mid-sentence (a long sentence force-split on word
# boundaries) must sound CONTINUOUS -- a full sentence pause there lands as a stutter in mid-clause --
# while a question needs a little more air after it than a plain statement.
_PAUSE_SCALE: dict[str, float] = {
    "": 0.25,  # no terminal punctuation => a forced mid-sentence split
    ",": 0.5,
    ";": 0.7,
    ":": 0.7,
    ".": 1.0,
    "?": 1.25,
    "!": 1.25,
}
_PAUSE_TRAILING = " \t\n\"')]}»”’"  # closing marks that can sit AFTER the real punctuation


def pause_after_ms(chunk: str, *, base: int) -> int:
    """Pause to insert after ``chunk`` when stitching chunks back into one scene, scaled from ``base``
    by the punctuation the chunk ends on (see ``_PAUSE_SCALE``). Chunks are synthesized independently,
    so WITHOUT this every join gets the SAME gap and the narration pauses on a metronome instead of on
    the meaning. Pure + deterministic, so it is unit-tested directly."""
    text = (chunk or "").rstrip(_PAUSE_TRAILING)
    scale = _PAUSE_SCALE.get(text[-1] if text else "", _PAUSE_SCALE[""])
    return max(0, int(round(max(0, base) * scale)))


def best_reference_window(voiced: list[bool], *, window: int) -> tuple[int, int]:
    """Pick the ``window``-frame slice of a voice-cloning reference holding the MOST speech.

    Chatterbox conditions on only the FIRST few seconds of the reference clip and discards the rest,
    so leading silence and dead air inside that window are pure loss: they are the model's entire
    picture of how this speaker sounds. Returns ``(start, end)`` frame indices; ties keep the EARLIEST
    window. Pure + deterministic, so it is unit-tested directly."""
    n = len(voiced)
    if window <= 0 or n <= window:
        return (0, n)
    running = 0
    counts = [0]
    for v in voiced:
        running += 1 if v else 0
        counts.append(running)
    best_start, best = 0, -1
    for s in range(n - window + 1):
        c = counts[s + window] - counts[s]
        if c > best:
            best, best_start = c, s
    return (best_start, best_start + window)


# A cloning reference is not one uniform performance: MEASURED across 46 twelve-second windows of the
# operator's own 102 s recording, pace varied by 46% and dynamic range by 27% while the pitch MEDIAN
# moved only 10% -- so the window choice is a real tone control (see ``TONE_WEIGHTS`` at the top).
def _normalize(values: list[float]) -> list[float]:
    """Min-max a feature across candidate windows; an all-equal feature becomes neutral 0.5."""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def tone_scores(profiles: list[dict[str, float]], tone: str) -> list[float]:
    """Score every candidate reference window against a named ``tone`` (see ``TONE_WEIGHTS``).

    Each feature is min-max normalised ACROSS THE CANDIDATES first, so scoring is about which window
    is most X *for this speaker*, never against an absolute scale that would differ per microphone.
    An unknown tone falls back to ``DEFAULT_TONE``. Pure + deterministic, so it is unit-tested."""
    if not profiles:
        return []
    weights = TONE_WEIGHTS.get(tone, TONE_WEIGHTS[DEFAULT_TONE])
    columns = {f: _normalize([float(p.get(f, 0.0)) for p in profiles]) for f in TONE_FEATURES}
    return [
        sum(weights.get(f, 0.0) * columns[f][i] for f in TONE_FEATURES)
        for i in range(len(profiles))
    ]


def pick_toned_window(profiles: list[dict[str, float]], tone: str) -> int:
    """Index of the reference window that best matches ``tone``; ties keep the EARLIEST window.

    Windows whose pitch median strays far from the clip's own median are DROPPED first: pitch median
    is the identity-carrying feature (it moved only 10% across the real clip), so an outlier there is
    a recording artefact or a different voice, and cloning from it would change who the video sounds
    like. Returns 0 for an empty list. Pure + deterministic, so it is unit-tested directly."""
    if not profiles:
        return 0
    pitches = sorted(float(p.get("pitch_hz", 0.0)) for p in profiles)
    median = pitches[len(pitches) // 2]
    eligible = [
        i
        for i, p in enumerate(profiles)
        if median <= 0 or abs(float(p.get("pitch_hz", median)) - median) <= 0.25 * median
    ]
    if not eligible:
        eligible = list(range(len(profiles)))
    scores = tone_scores(profiles, tone)
    return max(eligible, key=lambda i: (scores[i], -i))


def _window_profiles(  # pragma: no cover - needs librosa + real audio
    audio, rate: int, *, window: int, hop: int, step: int
) -> tuple[list[dict[str, float]], list[int]]:
    """Acoustic profile of every candidate window: density, dynamics, pitch spread, pace, pitch median.

    Frame-level features are computed ONCE over the whole clip and then sliced per window (O(n) rather
    than re-analysing each window), because a 100 s reference yields dozens of overlapping candidates.
    Returns ``(profiles, start_frames)``."""
    import librosa
    import numpy as np

    frames = audio[: audio.size // hop * hop].reshape(-1, hop)
    level = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
    voiced = level > (level.max() - 35.0)
    f0 = librosa.yin(audio, fmin=60, fmax=350, sr=rate, frame_length=1024, hop_length=hop)
    f0 = np.asarray(f0)[: len(level)]
    f0 = np.where(np.isfinite(f0) & (f0 > 60) & (f0 < 350), f0, np.nan)
    if f0.size < len(level):  # yin can return one frame fewer
        f0 = np.pad(f0, (0, len(level) - f0.size), constant_values=np.nan)
    onset_frames = librosa.onset.onset_detect(y=audio, sr=rate, hop_length=hop, units="frames")
    onset_hits = np.zeros(len(level), dtype=bool)
    onset_hits[np.clip(onset_frames, 0, len(level) - 1)] = True

    profiles: list[dict[str, float]] = []
    starts: list[int] = []
    for s in range(0, max(1, len(level) - window + 1), max(1, step)):
        sl = slice(s, s + window)
        win_voiced = voiced[sl]
        if win_voiced.sum() < 5:
            continue
        vdb = level[sl][win_voiced]
        win_f0 = f0[sl]
        win_f0 = win_f0[np.isfinite(win_f0)]
        seconds = window * hop / rate
        profiles.append(
            {
                "density": float(win_voiced.mean()),
                "dynamics": float(vdb.std()),
                "pitch": float(np.std(win_f0)) if win_f0.size else 0.0,
                "pitch_hz": float(np.median(win_f0)) if win_f0.size else 0.0,
                "pace": float(onset_hits[sl].sum()) / seconds if seconds else 0.0,
            }
        )
        starts.append(s)
    return profiles, starts


def _prepare_reference(
    path: str, *, window_sec: float, tone: str = DEFAULT_TONE
) -> str:  # pragma: no cover - needs librosa + real audio
    """Write a trimmed copy of the cloning reference holding the ``window_sec`` of speech whose
    DELIVERY best matches ``tone``, and return its path (see ``ChatterboxTTS._conditioning_clip``).
    Returns ``path`` unchanged when the clip is already short enough, when trimming is disabled
    (``window_sec`` <= 0), or on ANY error -- the original clip always remains a working fallback."""
    if not path or window_sec <= 0:
        return path
    try:
        import hashlib
        import tempfile
        from pathlib import Path

        import librosa
        import numpy as np
        import soundfile as sf

        audio, rate = librosa.load(path, sr=None, mono=True)
        need = int(window_sec * rate)
        if audio.size == 0 or audio.size <= need:
            return path
        hop = max(1, int(0.03 * rate))
        window = max(1, need // hop)
        profiles, starts = _window_profiles(
            audio, rate, window=window, hop=hop, step=max(1, window // 6)
        )
        if profiles:
            best = pick_toned_window(profiles, tone)
            start = starts[best]
            chosen = profiles[best]
        else:  # every window was near-silent -- fall back to the plain densest-speech scan
            frames = audio[: audio.size // hop * hop].reshape(-1, hop)
            level = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
            start, _ = best_reference_window((level > (level.max() - 35.0)).tolist(), window=window)
            chosen = {}
        clip = audio[start * hop : (start + window) * hop]
        if clip.size == 0:
            return path
        key = f"{Path(path).resolve()}|{window_sec}|{tone}"
        stem = hashlib.sha256(key.encode()).hexdigest()[:16]
        out = Path(tempfile.gettempdir()) / f"cf_voice_ref_{stem}.wav"
        if not out.exists():
            sf.write(str(out), clip, rate)
        from ..logging import get_logger

        get_logger(component="tts").info(
            "reference_clip_condensed",
            tone=tone,
            source_sec=round(audio.size / rate, 1),
            used_sec=round(clip.size / rate, 1),
            offset_sec=round(start * hop / rate, 1),
            candidates=len(profiles),
            dynamics_db=round(chosen.get("dynamics", 0.0), 2),
            pace_per_sec=round(chosen.get("pace", 0.0), 2),
            density=round(chosen.get("density", 0.0), 2),
            path=str(out),
        )
        return str(out)
    except Exception:
        return path


def _keep_slices(
    n: int, silent: list[tuple[int, int]], *, pad: int, max_gap: int, edge_pad: int | None = None
) -> list[tuple[int, int]]:
    """Plan which sample slices of ``[0, n)`` to KEEP so leading/trailing silence is trimmed to
    ``edge_pad`` samples (defaulting to ``pad``) and any INTERNAL silent run longer than ``max_gap``
    (0 disables) collapses to ``2*pad`` samples. The two pads are separate because the caller now adds
    an explicit, punctuation-sized pause BETWEEN chunks: the edges are trimmed close so that inserted
    pause is what the listener hears, while internal collapsing still lands on a natural beat.
    ``silent`` = the maximal silent ``(start, end)`` runs, sorted, non-overlapping, inside
    ``[0, n)``. Voiced samples are NEVER cut. Pure + deterministic, so it is unit-tested directly."""
    edge = pad if edge_pad is None else edge_pad
    drops: list[tuple[int, int]] = []
    for s, e in silent:
        lead, trail = s <= 0, e >= n
        if lead and trail:
            continue  # the whole signal is silent -> leave it to the caller
        if lead:
            if e - s > edge:
                drops.append((s, e - edge))  # keep only `edge` before the first word
        elif trail:
            if e - s > edge:
                drops.append((s + edge, e))  # keep only `edge` after the last word
        elif max_gap and (e - s) > max_gap:
            drops.append((s + pad, e - pad))  # collapse a very long internal pause to ~2*pad
    keep: list[tuple[int, int]] = []
    cursor = 0
    for a, b in drops:
        if a > cursor:
            keep.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < n:
        keep.append((cursor, n))
    return keep


def _silent_runs(voiced) -> list[tuple[int, int]]:  # pragma: no cover - needs torch + real audio
    """Maximal silent ``(start, end)`` sample runs from a 1-D voiced (amp > thresh) boolean tensor."""
    import torch

    n = int(voiced.shape[-1])
    sil = ~voiced
    if not bool(sil.any()):
        return []
    flips = (torch.nonzero(sil[1:] != sil[:-1]).flatten() + 1).tolist()
    bounds = [0, *flips, n]
    return [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1) if bool(sil[bounds[k]])]


def _trim_silence(
    wav,
    sample_rate: int,
    *,
    thresh: float = 0.015,
    pad_ms: int = 40,
    max_pause_ms: int = 0,
    edge_pad_ms: int | None = None,
):  # pragma: no cover - needs torch + real audio
    """Trim leading/trailing near-silence from a Chatterbox waveform tensor (channels, samples),
    keeping an ``edge_pad_ms`` pad (default ``pad_ms``), and (when ``max_pause_ms`` > 0) collapse any
    INTERNAL silent run longer than it to ~2*``pad_ms`` so a rare 2-3 s dead-air gap between two
    sentences in a chunk stops being an outlier. Only SILENT samples are ever removed (speech is
    untouched), and pauses shorter than the cap are left byte-identical. Returns the tensor unchanged
    when it is all silence or on any error."""
    try:
        import torch

        amp = wav.abs()
        if amp.dim() == 2:
            amp = amp.mean(dim=0)
        n = int(amp.shape[-1])
        voiced = amp > thresh
        if not bool(voiced.any()):
            return wav
        pad = int(sample_rate * pad_ms / 1000)
        edge = pad if edge_pad_ms is None else int(sample_rate * edge_pad_ms / 1000)
        max_gap = int(sample_rate * max_pause_ms / 1000) if max_pause_ms else 0
        keep = _keep_slices(n, _silent_runs(voiced), pad=pad, max_gap=max_gap, edge_pad=edge)
        if not keep:
            return wav
        if len(keep) == 1:
            a, b = keep[0]
            return wav[..., a:b]
        return torch.cat([wav[..., a:b] for a, b in keep], dim=-1)
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
        raise TTSError(
            "ffmpeg is required to encode Piper audio to mp3; install ffmpeg (see README)."
        )
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-f",
            "mp3",
            "pipe:1",
        ],
        input=wav_bytes,
        capture_output=True,
        check=False,
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
