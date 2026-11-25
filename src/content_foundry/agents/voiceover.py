"""Agent 4 — Voiceover / TTS. Narration + word/scene timings, the timing backbone (Ch. 10)."""

from __future__ import annotations

import contextlib
import hashlib
import json
from io import BytesIO
from pathlib import Path

from ..errors import TTSError
from ..logging import get_logger
from ..models import Provenance, SceneTiming, Script, VoiceoverAsset, WordTiming
from ..models.voiceover import SceneAudio

_WORDS_PER_SEC = 2.5
_AUDIO_REL = "assets/narration.mp3"
# Kept beside the audio it produced, so the operator can read what the narrator was told to say and
# correct any line by hand -- a hand-edited entry is preserved on a re-voice rather than re-asked.
_PRONUNCIATION_REL = "assets/pronunciations.json"

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


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _synthesis_signature(settings, provider, tone: str) -> str:
    from ..providers.text_normalize import RULES_VERSION

    values = {
        key: value
        for key, value in settings.model_dump(mode="json").items()
        if key.startswith(("tts_", "indextts_", "elevenlabs_", "piper_"))
    }
    reference = Path(settings.tts_reference_clip) if settings.tts_reference_clip else None
    values["reference_sha256"] = (
        hashlib.sha256(reference.read_bytes()).hexdigest()
        if reference and reference.is_file()
        else ""
    )
    values.update(
        tone=tone,
        rules_version=RULES_VERSION,
        provider=getattr(provider, "name", settings.tts_provider),
        voice=getattr(provider, "voice", settings.tts_voice_id),
        provider_class=f"{type(provider).__module__}.{type(provider).__qualname__}",
    )
    return _digest(values)


def _assembly_signature(settings, scenes, scene_audio: list[SceneAudio]) -> str:
    return _digest(
        {
            "version": 1,
            "lead_in": settings.voiceover_lead_in_sec,
            "sfx_gap": settings.sfx_gap_sec,
            "scenes": [(scene.index, scene.narration, scene.sfx) for scene in scenes],
            "audio": [item.model_dump(mode="json") for item in scene_audio],
        }
    )


def _read_scene_audio(run_root: Path, item: SceneAudio) -> bytes:
    target = (run_root / item.path).resolve()
    if not target.is_relative_to((run_root / "assets" / "voiceover").resolve()):
        raise TTSError(f"Scene {item.scene_index}: audio must be inside assets/voiceover.")
    try:
        audio = target.read_bytes()
    except OSError as exc:
        raise TTSError(f"Scene {item.scene_index}: saved audio is missing or unreadable.") from exc
    if not audio:
        raise TTSError(f"Scene {item.scene_index}: saved audio is empty.")
    return audio


def tone_for_script(template_id: str, *, override: str = "") -> str:
    """Delivery tone for a script: an explicit ``override`` wins, else it is derived from the
    template. ``override='auto'`` (or blank) means derive. Pure, so it is unit-tested directly."""
    choice = (override or "").strip().lower()
    if choice and choice != "auto":
        return choice
    return _TEMPLATE_TONES.get((template_id or "").strip().lower(), "neutral")


class Voiceover:
    def __init__(self, settings, tts_provider, llm_provider=None):
        self._settings = settings
        self._tts = tts_provider
        self._llm = llm_provider
        self._log = get_logger(component="voiceover")

    def _pronunciations(self, script: Script, run_root: Path) -> dict[str, str]:
        """How to say every abbreviation in this script. Best-effort: {} keeps the curated tables."""
        from ..providers.text_normalize import RULES_VERSION

        path = run_root / _PRONUNCIATION_REL
        known: dict[str, str] = {}
        if path.exists():
            with contextlib.suppress(OSError, ValueError):
                loaded = json.loads(path.read_text(encoding="utf-8"))
                version = loaded.get("rules_version") if isinstance(loaded, dict) else None
                entries = loaded.get("pronunciations") if isinstance(loaded, dict) else None
                if version == RULES_VERSION and isinstance(entries, dict):
                    known = {str(k): str(v) for k, v in entries.items() if v}
                else:
                    # Written under different spelling rules (or the older un-stamped format), so
                    # every entry in it is an answer to a question we no longer ask. Re-derive rather
                    # than hand the voice a rendering the current rules would never produce.
                    self._log.info(
                        "pronunciations_discarded",
                        found=version,
                        expected=RULES_VERSION,
                        hint="rules changed since this run was last voiced; re-resolving",
                    )
        try:
            from .pronunciation import PronunciationDirector

            text = " ".join(s.narration for s in script.scenes)
            resolved = PronunciationDirector(self._settings, self._llm).resolve(text, known=known)
        except Exception as exc:  # never let a pronunciation lookup break synthesis
            self._log.warning("pronunciation_skipped", error=str(exc))
            return known
        if resolved and resolved != known:
            with contextlib.suppress(OSError):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {"rules_version": RULES_VERSION, "pronunciations": resolved},
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
        return resolved

    def run(
        self,
        run_id: str,
        script: Script,
        *,
        run_root: Path,
        scene_indices: set[int] | None = None,
        previous: VoiceoverAsset | None = None,
    ) -> VoiceoverAsset:
        scenes = sorted(script.scenes, key=lambda scene: scene.index)
        indices = {scene.index for scene in scenes}
        if len(indices) != len(scenes):
            raise TTSError("Scene indices must be unique.")
        if scene_indices is not None and (not scene_indices or not scene_indices <= indices):
            raise TTSError("Select at least one existing scene index.")
        saved = {item.scene_index: item for item in previous.scene_audio} if previous else {}
        if scene_indices is not None and (
            not previous or len(saved) != len(previous.scene_audio) or set(saved) != indices
        ):
            raise TTSError(
                "Selective voiceover needs saved audio for every scene; run a full voiceover first."
            )
        # 0) Tell a cloning provider WHICH delivery to imitate before it prepares its reference.
        # Best-effort: providers without a tone (ElevenLabs/Edge/OpenAI/Piper) simply don't have the
        # hook, so this is a no-op for them.
        tone = tone_for_script(script.template_id, override=getattr(self._settings, "tts_tone", ""))
        setter = getattr(self._tts, "set_tone", None)
        if callable(setter):
            setter(tone)
            self._log.info("voice_tone_selected", tone=tone, template=script.template_id)

        # 0b) And HOW to say this script's abbreviations. Same best-effort shape as the tone hook.
        say = getattr(self._tts, "set_pronunciations", None)
        pronunciations = {}
        if callable(say):
            pronunciations = self._pronunciations(script, run_root)
            say(pronunciations)

        from ..providers.text_normalize import speechify_numbers

        signature = _synthesis_signature(self._settings, self._tts, tone)
        spoken = {
            scene.index: speechify_numbers(scene.narration, pronunciations)
            if callable(say)
            else scene.narration
            for scene in scenes
        }
        reused: dict[int, bytes] = {}
        if scene_indices is not None:
            if previous is None or previous.synthesis_signature != signature:
                raise TTSError(
                    "Voice settings changed; run a full voiceover to avoid mixing voices or processing."
                )
            for scene in scenes:
                if scene.index in scene_indices:
                    continue
                item = saved[scene.index]
                if item.narration != scene.narration or item.spoken_text != spoken[scene.index]:
                    raise TTSError(
                        f"Scene {scene.index} text or pronunciation changed; select that scene too."
                    )
                audio = _read_scene_audio(run_root, item)
                if hashlib.sha256(audio).hexdigest() != item.sha256:
                    raise TTSError(
                        f"Scene {scene.index} audio was replaced; render it before selectively re-voicing."
                    )
                reused[scene.index] = audio

        # 1) Synthesize every scene up front, keeping each provider's raw audio + its timings/estimate.
        chunks: list[bytes] = []
        provider_timings: list[list[WordTiming] | None] = []
        estimates: list[float] = []
        scene_audio: list[SceneAudio] = []
        for scene in scenes:
            if scene.index in reused:
                chunk, timings = reused[scene.index], saved[scene.index].word_timings
            else:
                chunk, timings = self._tts.synthesize(scene.narration)
            digest = hashlib.sha256(chunk).hexdigest()
            relative = f"assets/voiceover/scene_{scene.index}_{digest[:16]}.mp3"
            target = run_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(chunk)
            scene_audio.append(
                SceneAudio(
                    scene_index=scene.index,
                    path=relative,
                    narration=scene.narration,
                    sha256=digest,
                    spoken_text=spoken[scene.index],
                    word_timings=timings,
                )
            )
            chunks.append(chunk)
            provider_timings.append(timings)
            estimates.append(
                max((t.end for t in timings), default=_estimate(scene.narration))
                if timings
                else _estimate(scene.narration)
            )

        return self._assemble(
            run_id, script, run_root, chunks, provider_timings, estimates, scene_audio, signature
        )

    def _assemble(
        self,
        run_id,
        script,
        run_root,
        chunks,
        provider_timings,
        estimates,
        scene_audio,
        synthesis_signature,
        *,
        strict=False,
    ) -> VoiceoverAsset:
        scenes = sorted(script.scenes, key=lambda scene: scene.index)
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
            if strict:
                raise TTSError(
                    "Replacement audio could not be decoded; narration was not overwritten."
                )
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
        temporary = audio_path.with_suffix(".tmp.mp3")
        temporary.write_bytes(audio_bytes)
        temporary.replace(audio_path)

        return VoiceoverAsset(
            run_id=run_id,
            audio_path=_AUDIO_REL,
            duration_sec=round(cursor, 3),
            sample_rate=getattr(self._tts, "sample_rate", 44100),
            voice_id=getattr(self._tts, "voice", "") or self._settings.tts_voice_id,
            provider=getattr(self._tts, "name", self._settings.tts_provider),
            word_timings=word_timings,
            scene_timings=scene_timings,
            scene_audio=scene_audio,
            synthesis_signature=synthesis_signature,
            assembly_signature=_assembly_signature(self._settings, scenes, scene_audio),
            provenance=Provenance(
                produced_by="voiceover", model=None, config_hash=self._settings.config_hash
            ),
        )


def reassemble_saved(
    settings, script: Script, asset: VoiceoverAsset, *, run_root: Path
) -> VoiceoverAsset:
    """Rebuild edited scene audio without synthesis; legacy joined tracks remain untouched."""
    if not asset.scene_audio:
        return asset
    scenes = sorted(script.scenes, key=lambda scene: scene.index)
    saved = {item.scene_index: item for item in asset.scene_audio}
    if len(saved) != len(asset.scene_audio) or set(saved) != {scene.index for scene in scenes}:
        raise TTSError("Scene layout changed; run a full voiceover before rendering.")
    chunks = []
    records = []
    replaced = False
    for scene in scenes:
        item = saved[scene.index]
        if item.narration != scene.narration:
            raise TTSError(
                f"Scene {scene.index} narration changed; re-voice that scene before rendering."
            )
        audio = _read_scene_audio(run_root, item)
        digest = hashlib.sha256(audio).hexdigest()
        if digest != item.sha256:
            item = item.model_copy(update={"sha256": digest, "word_timings": None})
            replaced = True
        records.append(item)
        chunks.append(audio)
    signature = _assembly_signature(settings, scenes, records)
    if signature == asset.assembly_signature and (run_root / asset.audio_path).is_file():
        return asset
    estimates = [
        max((word.end for word in item.word_timings), default=_estimate(item.narration))
        if item.word_timings
        else _estimate(item.narration)
        for item in records
    ]
    assembled = Voiceover(settings, None)._assemble(
        asset.run_id,
        script,
        run_root,
        chunks,
        [item.word_timings for item in records],
        estimates,
        records,
        asset.synthesis_signature,
        strict=replaced,
    )
    return assembled.model_copy(
        update={
            "provider": asset.provider,
            "voice_id": asset.voice_id,
            "sample_rate": asset.sample_rate,
        }
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
