"""Persistent IndexTTS-2 worker. Runs in the index-tts venv, NOT in content-foundry's.

IndexTTS-2 requires numpy>=2 and a newer transformers than Chatterbox's pinned stack, so the two
models can never share an interpreter. This script is therefore executed by ANOTHER python -- it
must not import ``content_foundry`` or anything else from our package.

Loading the model costs tens of seconds, so a per-call subprocess would be unusable across the
dozens of chunks in one video. Instead this stays alive and speaks one JSON object per line on
stdin/stdout::

    -> {"text": "...", "out": "C:/tmp/x.wav", "speaker": "ref.wav", "emo_alpha": 0.6, ...}
    <- {"ok": true, "out": "C:/tmp/x.wav"}
    <- {"ok": false, "error": "..."}

Model chatter is redirected to stderr so stdout carries ONLY protocol lines.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import traceback
from typing import Any

_TTS: Any = None


def _cast_tree(obj: Any, dtype: Any) -> Any:
    """Cast floating tensors in a forward's args/returns, leaving ints/None/etc alone."""
    import torch

    if torch.is_tensor(obj):
        return obj.to(dtype) if obj.is_floating_point() else obj
    if isinstance(obj, list | tuple):
        return type(obj)(_cast_tree(o, dtype) for o in obj)
    if getattr(obj, "hidden_states", None) is not None:
        obj.hidden_states = tuple(_cast_tree(h, dtype) for h in obj.hidden_states)
        if getattr(obj, "last_hidden_state", None) is not None:
            obj.last_hidden_state = _cast_tree(obj.last_hidden_state, dtype)
        return obj
    return obj


def _shrink_to_fit(tts: Any, precision: str = "fp16") -> list[str]:
    """Get IndexTTS-2 under a 6 GB card, because overshooting it is catastrophically slow.

    ``use_fp16=True`` only halves the GPT; s2mel, w2v-bert, BigVGAN and the codec all stay fp32.
    MEASURED on a 6144 MiB (6442 MB) RTX 3060: a real 57-word chunk peaks at **7173 MB**, so the
    Windows driver silently pages VRAM to system RAM and every diffusion step crosses PCIe --
    s2mel alone took 161 s. Two frees bring the peak to 5983 MB, UNDER the card:

      * ``qwen_emo`` (1192 MB) is loaded unconditionally but only serves TEXT-driven emotion
        (``use_emo_text``), which we never request -- pure ballast on the GPU.
      * ``semantic_model`` (w2v-bert, 2322 MB fp32) halves to ~1161 MB. Its inputs arrive fp32 and
        its hidden states feed fp32 consumers, so forward is wrapped to cast DOWN on the way in and
        back UP on the way out; without that boundary it raises "expected scalar type Float".

    NOTE ON PRECISION: this model does NOT synthesise audio -- it encodes the speaker reference, so
    its dtype affects VOICE SIMILARITY, not waveform fidelity (s2mel and BigVGAN, which actually make
    the audio, stay fp32 throughout). ``precision`` picks its dtype: ``fp16`` (10 mantissa bits, the
    most precise of the two 16-bit formats), ``bf16`` (only 7 mantissa bits but fp32's exponent
    range, so it cannot overflow; same size, native on Ampere+), or ``fp32`` to skip the conversion
    entirely when the VRAM can be found elsewhere.

    End to end this took the same chunk from 551.5 s to 71.8 s (7.7x) with the audio unchanged in
    character (RMS 697, still real speech). Every step is best-effort: a failure just leaves that
    component as it was, so the worst case is the old slow-but-working behaviour.
    """
    notes: list[str] = []
    try:
        import torch
    except Exception as exc:  # pragma: no cover - torch is always present in that venv
        return [f"torch unavailable: {exc}"]

    dtypes = {"fp16": torch.float16, "bf16": torch.bfloat16, "fp32": torch.float32}
    target = dtypes.get(precision, torch.float16)

    def alloc() -> float:
        return torch.cuda.memory_allocated() / 1e6 if torch.cuda.is_available() else 0.0

    start = alloc()
    try:
        holder = getattr(tts, "qwen_emo", None)
        model = getattr(holder, "model", None) if holder is not None else None
        if isinstance(model, torch.nn.Module):
            model.to("cpu")
            torch.cuda.empty_cache()
            notes.append(f"qwen_emo -> CPU ({start:.0f} -> {alloc():.0f} MB)")
    except Exception as exc:
        notes.append(f"qwen_emo not freed: {exc}")

    if target is torch.float32:
        notes.append(f"semantic_model left fp32 ({alloc():.0f} MB)")
        return notes
    try:
        before = alloc()
        semantic = tts.semantic_model.to(target).eval()
        original = semantic.forward

        def _forward(*args: Any, **kwargs: Any) -> Any:
            args = tuple(_cast_tree(a, target) for a in args)
            kwargs = {k: _cast_tree(v, target) for k, v in kwargs.items()}
            return _cast_tree(original(*args, **kwargs), torch.float32)

        semantic.forward = _forward
        tts.semantic_model = semantic
        torch.cuda.empty_cache()
        notes.append(f"semantic_model -> {precision} ({before:.0f} -> {alloc():.0f} MB)")
    except Exception as exc:
        notes.append(f"semantic_model not converted: {exc}")
    return notes


def _load(
    cfg_path: str,
    model_dir: str,
    *,
    fp16: bool,
    cuda_kernel: bool,
    deepspeed: bool,
    precision: str = "fp16",
) -> Any:
    global _TTS
    if _TTS is None:
        from indextts.infer_v2 import IndexTTS2

        # The constructor prints progress; keep stdout clean for the protocol.
        with contextlib.redirect_stdout(sys.stderr):
            _TTS = IndexTTS2(
                cfg_path=cfg_path,
                model_dir=model_dir,
                use_fp16=fp16,
                use_cuda_kernel=cuda_kernel,
                use_deepspeed=deepspeed,
            )
            for note in _shrink_to_fit(_TTS, precision):
                print(f">> {note}", file=sys.stderr)
    return _TTS


def _synthesize(tts: Any, job: dict) -> None:
    kwargs: dict[str, Any] = {
        "spk_audio_prompt": job["speaker"],
        "text": job["text"],
        "output_path": job["out"],
        "verbose": False,
    }
    # Emotion is OPTIONAL and off unless asked for: a plain clone is the honest baseline to compare
    # against Chatterbox. An 8-float vector is [happy, angry, sad, afraid, disgusted, melancholic,
    # surprised, calm].
    vector = job.get("emo_vector")
    if vector:
        kwargs["emo_vector"] = list(vector)
        kwargs["use_random"] = False
    reference = job.get("emo_audio")
    if reference:
        kwargs["emo_audio_prompt"] = reference
    if (vector or reference) and job.get("emo_alpha") is not None:
        kwargs["emo_alpha"] = float(job["emo_alpha"])
    with contextlib.redirect_stdout(sys.stderr):
        tts.infer(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="IndexTTS-2 line-protocol worker.")
    parser.add_argument("--cfg", required=True, help="checkpoints/config.yaml")
    parser.add_argument("--model-dir", required=True, help="checkpoints directory")
    parser.add_argument("--fp16", action="store_true", help="half precision (lower VRAM)")
    parser.add_argument("--cuda-kernel", action="store_true")
    parser.add_argument("--deepspeed", action="store_true")
    parser.add_argument(
        "--precision",
        default="fp16",
        choices=("fp16", "bf16", "fp32"),
        help="dtype for the speaker-encoding model; audio synthesis stays fp32 regardless",
    )
    args = parser.parse_args()

    def reply(payload: dict) -> None:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()

    try:
        tts = _load(
            args.cfg,
            args.model_dir,
            fp16=args.fp16,
            cuda_kernel=args.cuda_kernel,
            deepspeed=args.deepspeed,
            precision=args.precision,
        )
    except Exception as exc:  # loading failed -> say so once, then exit non-zero
        reply({"ok": False, "error": f"model load failed: {exc}", "fatal": True})
        traceback.print_exc(file=sys.stderr)
        return 1

    reply({"ok": True, "ready": True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except Exception as exc:
            reply({"ok": False, "error": f"bad request: {exc}"})
            continue
        if job.get("stop"):
            return 0
        try:
            _synthesize(tts, job)
            reply({"ok": True, "out": job.get("out", "")})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            reply({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
