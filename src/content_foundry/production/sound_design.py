"""Sound design: mix script sound-effect cues into the narration at their scene-start times (Ch. 12.4)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Two effects must never sound at once (a scene cue and the subscribe bell can land on the same
# beat) — the stack reads as one muddy hit instead of two intents. A colliding effect is pushed to
# just after the previous one ends, with this much air between them.
_SFX_MIN_GAP_SEC = 0.15
# ...but only so far. Past this an effect no longer reads as *that* moment's punctuation, so it is
# dropped instead of firing at the wrong line.
_SFX_MAX_SHIFT_SEC = 2.0
# Fade applied when an effect has to be trimmed, so it tapers instead of cutting off mid-sound.
_SFX_FADE_MS = 120


def _relative_gain(base_dbfs: float, sfx_dbfs: float, gain_db: float) -> float:
    """dB delta to apply to an SFX so it lands ``gain_db`` dB relative to the NARRATION's loudness
    (``base_dbfs``). Returns 0.0 for a silent/undecodable narration or SFX (a non-finite dBFS) so the
    clip is left alone. This makes SFX_VOLUME_DB behave as documented — relative to the voice — so a
    loud stock clip no longer stays loud and LOWERING the value reliably makes every effect quieter,
    regardless of each clip's own baked-in level."""
    import math

    if not math.isfinite(sfx_dbfs) or not math.isfinite(base_dbfs):
        return 0.0
    return (base_dbfs + gain_db) - sfx_dbfs


def plan_sfx_positions(cues: Sequence[tuple[float, float]]) -> list[float | None]:
    """Place effects so they sound ONE AT A TIME. ``cues`` is ``(start_sec, duration_sec)`` ordered by
    start; returns the position for each cue, or ``None`` for one that must be dropped.

    Effects still play UNDER the narration (that's what an effect is) but never under EACH OTHER: two
    that land on the same beat — typically a scene cue and the subscribe bell — stack into one muddy
    hit that reads as neither. The later one waits for the earlier to finish, unless that would drag
    it more than ``_SFX_MAX_SHIFT_SEC`` from its own moment, in which case it is dropped rather than
    fired against the wrong line.
    """
    positions: list[float | None] = []
    busy_until = 0.0  # when the previously placed effect finishes
    for start, duration in cues:
        position = max(start, busy_until)
        if position - start > _SFX_MAX_SHIFT_SEC:
            positions.append(None)
            continue
        positions.append(position)
        busy_until = position + max(0.0, duration) + _SFX_MIN_GAP_SEC
    return positions


def mix_sfx(
    narration_path: str,
    cues: Sequence[tuple[float, str]],
    sfx_client,
    out_path: str | Path,
    *,
    gain_db: float = -8.0,
    max_len_sec: float = 0.0,
) -> bool:
    """Overlay each resolved SFX onto the narration at its ``start`` (seconds) and write ``out_path``.

    ``gain_db`` is applied RELATIVE TO THE NARRATION's loudness (e.g. -8 => each effect sits 8 dB under
    the voice), so a loud stock clip is tamed to a predictable level and lowering the value reliably
    makes effects quieter. ``max_len_sec`` is the silent beat the voiceover reserved ahead of the
    narration for this effect: anything longer is trimmed (with a short fade) so a 3 s stock clip
    can't run past the gap and play over the first words. Returns True when the mixed file was written
    (at least one cue resolved to a real clip), else False so the caller keeps the original narration.
    A bad clip is skipped, never fatal.
    """
    resolved: list[tuple[float, str]] = []
    for start, keyword in cues:
        path = sfx_client.resolve(keyword) if keyword else None
        if path:
            resolved.append((max(0.0, float(start)), path))
    if not resolved:
        return False
    try:
        from pydub import AudioSegment  # lazy: optional dependency
    except ImportError:  # pragma: no cover - pydub optional
        return False

    try:
        base = AudioSegment.from_file(narration_path)
        base_dbfs = (
            base.dBFS
        )  # the voice's loudness -> every SFX is set RELATIVE to it (not its own)
        loaded: list[tuple[float, Any]] = []
        for start, path in sorted(resolved, key=lambda c: c[0]):
            try:
                sfx = AudioSegment.from_file(path)
                sfx = sfx.apply_gain(_relative_gain(base_dbfs, sfx.dBFS, gain_db))
                # Keep the effect inside the silence reserved for it, fading rather than hard-cutting.
                if max_len_sec > 0 and len(sfx) > max_len_sec * 1000:
                    sfx = sfx[: int(max_len_sec * 1000)].fade_out(_SFX_FADE_MS)
            except Exception:  # pragma: no cover - a bad clip must not kill the render
                continue
            loaded.append((start, sfx))
        positions = plan_sfx_positions([(start, len(sfx) / 1000.0) for start, sfx in loaded])
        for (_, sfx), position in zip(loaded, positions, strict=True):
            if position is None:
                continue
            base = base.overlay(sfx, position=int(position * 1000))
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        base.export(str(out), format="mp3")
    except Exception:
        # Mixing must NEVER break the render (e.g. an undecodable narration) — fall back to the
        # untouched narration by reporting failure to the caller.
        return False
    return True


def _write_pcm16_wav(path: Path, samples, sample_rate: int = 44100) -> Path:
    """Write a float32/float64 [-1, 1] mono numpy array as 16-bit PCM WAV."""
    import wave

    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -0.98, 0.98)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return path


def ensure_transition_sfx_wav(target_dir: str | Path, kind: str = "ui_tick") -> Path:
    """Deterministically synthesize clean, subtle procedural fallback transition WAVs
    (``ui_tick.wav`` or ``soft_whoosh.wav``) if not already present on disk."""
    import numpy as np

    folder = Path(target_dir)
    name = "soft_whoosh" if kind == "soft_whoosh" else "ui_tick"
    out = folder / f"{name}.wav"
    if out.is_file() and out.stat().st_size > 128:
        return out

    sr = 44100
    if name == "ui_tick":
        dur = 0.075
        n = int(sr * dur)
        t = np.linspace(0.0, dur, n, endpoint=False)
        # Pitch-dropping soft sine transient (820 Hz -> 380 Hz) with smooth 3ms attack & fast decay.
        phase = 2.0 * np.pi * (820.0 * t - 2900.0 * (t**2))
        env = np.minimum(1.0, t / 0.003) * np.exp(-t / 0.016)
        sig = 0.55 * np.sin(phase) * env
    else:
        dur = 0.22
        n = int(sr * dur)
        t = np.linspace(0.0, dur, n, endpoint=False)
        rng = np.random.default_rng(20261125)
        white = rng.standard_normal(n)
        # Smooth low-pass moving average (< 1.6 kHz) so it sits gently under speech without hiss.
        kernel = np.hanning(34)
        kernel /= kernel.sum()
        filtered = np.convolve(white, kernel, mode="same")
        bell = np.sin(np.pi * (t / dur)) ** 2.0
        sub_sweep = 0.25 * np.sin(2.0 * np.pi * (140.0 * t + 110.0 * (t**2))) * bell
        peak = float(np.max(np.abs(filtered)) or 1.0)
        sig = 0.45 * (filtered / peak) * bell + sub_sweep

    return _write_pcm16_wav(out, sig, sample_rate=sr)


def ensure_dark_ambient_wav(target_path: str | Path, duration_sec: float = 16.0) -> Path:
    """Deterministically synthesize a warm, vocal-carved dark ambient drone pad WAV.

    Uses low fifth harmonics (55 Hz A1 + 82.5 Hz E2 + 110 Hz A2 + gentle 165 Hz E3) with a slow
    breathing LFO and zero energy in the 250 Hz - 4 kHz speech band so voiceover intelligibility
    remains crystal clear.
    """
    import numpy as np

    out = Path(target_path)
    sr = 22050
    # Build a phase-aligned 8.0s loop unit first so any video length renders in milliseconds.
    loop_sec = 8.0
    loop_n = int(sr * loop_sec)
    t = np.linspace(0.0, loop_sec, loop_n, endpoint=False)
    lfo = 0.78 + 0.22 * np.sin(2.0 * np.pi * (1.0 / loop_sec) * t)
    pad = (
        0.46 * np.sin(2.0 * np.pi * 55.0 * t)
        + 0.30 * np.sin(2.0 * np.pi * 82.5 * t + 0.4)
        + 0.16 * np.sin(2.0 * np.pi * 110.0 * t + 1.1)
        + 0.08
        * np.sin(2.0 * np.pi * 165.0 * t + 2.0)
        * (0.6 + 0.4 * np.cos(2.0 * np.pi * 0.25 * t))
    ) * lfo
    total_dur = max(1.0, float(duration_sec))
    total_n = int(round(sr * total_dur))
    reps = (total_n // loop_n) + 2
    full = np.tile(pad, reps)[:total_n].copy()

    fade_in_n = min(int(sr * 1.5), total_n // 3)
    fade_out_n = min(int(sr * 2.5), total_n // 3)
    if fade_in_n > 1:
        full[:fade_in_n] *= 0.5 * (1.0 - np.cos(np.pi * np.linspace(0.0, 1.0, fade_in_n)))
    if fade_out_n > 1:
        full[-fade_out_n:] *= 0.5 * (1.0 + np.cos(np.pi * np.linspace(0.0, 1.0, fade_out_n)))

    peak = float(np.max(np.abs(full)) or 1.0)
    full = 0.70 * (full / peak)
    return _write_pcm16_wav(out, full, sample_rate=sr)


def mix_studio_audio(
    narration_path: str | Path,
    out_path: str | Path,
    *,
    scene_sfx_cues: Sequence[tuple[float, str]] = (),
    sfx_client: Any = None,
    sfx_gain_db: float = -8.0,
    sfx_max_len_sec: float = 0.0,
    programmatic_sfx_cues: Sequence[tuple[float, str]] = (),
    programmatic_sfx_gain_db: float = -14.0,
    ambient_enabled: bool = False,
    ambient_gain_db: float = -30.0,
    ambient_music_path: str = "",
    work_dir: str | Path | None = None,
) -> bool:
    """Unified studio audio mixer combining:
      1. Scene-level SFX cues (at ``sfx_gain_db`` relative to voiceover, trimmed to ``sfx_max_len_sec``).
      2. Subtle programmatic graphic entrance ticks/whooshes (at ``programmatic_sfx_gain_db``).
      3. Continuous dark ambient background bed (custom ``ambient_music_path`` or procedural fallback)
         at ``ambient_gain_db`` (default ``-30 dB`` relative to narration) with gentle fades.
    Returns ``True`` when a mixed file was written, ``False`` on no-op or undecodable test audio.
    """
    has_scene_sfx = bool(
        scene_sfx_cues and sfx_client is not None and getattr(sfx_client, "enabled", False)
    )
    resolved_scene_sfx: list[tuple[float, str]] = []
    if has_scene_sfx:
        for start, keyword in scene_sfx_cues:
            resolved_p = sfx_client.resolve(keyword) if keyword else None
            if resolved_p:
                resolved_scene_sfx.append((max(0.0, float(start)), str(resolved_p)))

    has_prog_sfx = bool(programmatic_sfx_cues)
    if not resolved_scene_sfx and not has_prog_sfx and not ambient_enabled:
        return False

    try:
        from pydub import AudioSegment
    except ImportError:  # pragma: no cover
        return False

    try:
        base = AudioSegment.from_file(str(narration_path))
        base_dbfs = base.dBFS
        out = Path(out_path)
        cache_dir = Path(work_dir) if work_dir is not None else out.parent / "_synth_audio"

        # Collect all discrete SFX events (scene cues + programmatic graphic entrance cues) so
        # `plan_sfx_positions` guarantees zero overlapping hits across both streams.
        pending_sfx: list[tuple[float, Any]] = []
        for start, resolved_p in resolved_scene_sfx:
            try:
                clip = AudioSegment.from_file(resolved_p)
                clip = clip.apply_gain(_relative_gain(base_dbfs, clip.dBFS, sfx_gain_db))
                if sfx_max_len_sec > 0 and len(clip) > sfx_max_len_sec * 1000:
                    clip = clip[: int(sfx_max_len_sec * 1000)].fade_out(_SFX_FADE_MS)
                pending_sfx.append((start, clip))
            except Exception:
                continue

        if has_prog_sfx:
            for start, kind in programmatic_sfx_cues:
                try:
                    wav_p = ensure_transition_sfx_wav(cache_dir, kind=kind)
                    clip = AudioSegment.from_file(str(wav_p))
                    clip = clip.apply_gain(
                        _relative_gain(base_dbfs, clip.dBFS, programmatic_sfx_gain_db)
                    )
                    pending_sfx.append((max(0.0, float(start)), clip))
                except Exception:
                    continue

        wrote_anything = False
        if pending_sfx:
            pending_sfx.sort(key=lambda item: item[0])
            positions = plan_sfx_positions([(st, len(cl) / 1000.0) for st, cl in pending_sfx])
            for (_, clip), pos in zip(pending_sfx, positions, strict=True):
                if pos is None:
                    continue
                base = base.overlay(clip, position=int(pos * 1000))
                wrote_anything = True

        if ambient_enabled and len(base) > 200:
            custom = Path(ambient_music_path) if (ambient_music_path or "").strip() else None
            if custom is not None and custom.is_file():
                amb = AudioSegment.from_file(str(custom))
                if len(amb) < len(base):
                    reps = (len(base) // max(1, len(amb))) + 1
                    amb = (amb * reps)[: len(base)]
                else:
                    amb = amb[: len(base)]
            else:
                amb_wav = ensure_dark_ambient_wav(
                    cache_dir / "dark_ambient_fallback.wav",
                    duration_sec=len(base) / 1000.0,
                )
                amb = AudioSegment.from_file(str(amb_wav))[: len(base)]

            fi = min(1500, max(50, len(amb) // 4))
            fo = min(2500, max(50, len(amb) // 4))
            amb = amb.fade_in(fi).fade_out(fo)
            amb = amb.apply_gain(_relative_gain(base_dbfs, amb.dBFS, ambient_gain_db))
            base = base.overlay(amb, position=0)
            wrote_anything = True

        if not wrote_anything:
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".wav":
            base.export(str(out), format="wav")
        else:
            base.export(str(out), format="mp3", bitrate="256k")
        return True
    except Exception:
        return False
