"""Local face-identity thumbnail generation (option B) — the operator's OWN face, generated not pasted.

Generates the thumbnail image with the operator's face (from their avatar) AND the prompt's
emotion/scene in a single model pass: Stable Diffusion 1.5 + IP-Adapter-FaceID via ``diffusers``,
with the face embedding extracted by ``insightface``. Runs locally on the GPU (fits a ~6 GB card with
model CPU-offload), so there is no cut-out to composite (no choppy edges).

Heavy + OPTIONAL: torch/diffusers/insightface/onnxruntime/opencv are imported lazily and only when
``THUMBNAIL_FACE_ID_ENABLED=true``. ANY missing dependency, missing model, undetected face, or runtime
error returns ``None``, so the caller falls back to the normal composited thumbnail and the render
never breaks.

ONE-TIME SETUP (operator) — from the repo root::

    pip install -e ".[faceid]"

The pins matter: the voice clone (chatterbox) needs ``diffusers==0.29.0``, which an unpinned
``transformers``/``numpy`` upgrade would break, so the ``faceid`` extra pins ``transformers==4.44.2``
and ``numpy<2``. (torch + CUDA are already installed for the voice clone.) The SD checkpoint
(``FACEID_BASE_MODEL``) and
the IP-Adapter-FaceID weights (``h94/IP-Adapter-FaceID``) download from Hugging Face on first run;
insightface downloads its ``buffalo_l`` model automatically. Because I cannot run these models here,
the exact embed shape / model version may need a small tweak on your machine — until then it degrades
gracefully to the composited thumbnail.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from ..logging import get_logger

if TYPE_CHECKING:
    from ..config import Settings

_CACHE: dict = {}
_log = get_logger(component="faceid")


def _parse_wh(size: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        w, _, h = (size or "").partition("x")
        return int(w), int(h)
    except (TypeError, ValueError):
        return default


def generate_face_image(
    settings: Settings, *, prompt: str, face_path: str, size: str
) -> bytes | None:
    """Render a thumbnail-sized PNG of the operator's face + the prompt, or ``None`` on any problem
    (missing deps/model/face) so the caller can fall back to the composited thumbnail."""
    if not face_path or not Path(face_path).exists():
        _log.warning("faceid_no_face_image", path=str(face_path))
        return None
    try:
        return _generate(settings, prompt=prompt, face_path=face_path, size=size)
    except Exception as exc:  # pragma: no cover - requires the heavy GPU stack
        _log.warning(
            "faceid_unavailable",
            error=str(exc)[:300],
            hint='pip install -e ".[faceid]"  (pins transformers==4.44.2 / numpy<2 so the '
            "voice clone's diffusers==0.29.0 keeps working)",
        )
        return None


def _generate(settings, *, prompt, face_path, size) -> bytes | None:  # pragma: no cover - GPU/models
    import cv2
    import torch
    from PIL import Image

    device = _resolve_device(settings.faceid_device)
    dtype = torch.float16 if device == "cuda" else torch.float32

    img = cv2.imread(str(face_path))
    if img is None:
        return None
    faces = _face_app().get(img)
    if not faces:
        _log.warning("faceid_no_face_detected", path=str(face_path))
        return None
    # IP-Adapter-FaceID takes the insightface ID embedding (shape (1, 1, 512)), stacked with a zero
    # negative embedding for classifier-free guidance.
    ref = torch.from_numpy(faces[0].normed_embedding).unsqueeze(0)  # (1, 512)
    ref = torch.stack([ref], dim=0)  # (1, 1, 512)
    id_embeds = torch.cat([torch.zeros_like(ref), ref]).to(dtype=dtype, device=device)

    pipe = _pipeline(settings, device, dtype)
    gen_w, gen_h = _parse_wh(settings.faceid_gen_size, (768, 448))
    result = pipe(
        prompt=prompt,
        negative_prompt=settings.faceid_negative_prompt or None,
        ip_adapter_image_embeds=[id_embeds],
        num_inference_steps=int(settings.faceid_steps),
        guidance_scale=float(settings.faceid_guidance),
        width=gen_w,
        height=gen_h,
    )
    out_w, out_h = _parse_wh(size, (1280, 720))
    image = result.images[0].resize((out_w, out_h), Image.LANCZOS)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _resolve_device(pref: str) -> str:  # pragma: no cover - requires torch/GPU
    if (pref or "auto").lower() == "cpu":
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _prime_onnx_cuda_dlls() -> None:  # pragma: no cover - environment-specific DLL wiring
    """Put torch's bundled CUDA 12 runtime (cuBLAS/cuDNN/cuFFT/cuRAND/cudart) on the Windows DLL
    search path so onnxruntime's CUDA execution provider can load it. onnxruntime-gpu wheels do NOT
    ship the CUDA libraries, and in this environment they live ONLY inside ``torch/lib`` — without this
    the CUDA provider fails (``cublasLt64_12.dll`` not found) and insightface silently drops to slow
    CPU. Best-effort + idempotent; a no-op off Windows or when torch is unavailable. (onnxruntime-gpu
    must be the CUDA 12 build, matching torch's cu124.)"""
    if _CACHE.get("cuda_dlls_primed"):
        return
    _CACHE["cuda_dlls_primed"] = True  # attempt once; do not retry on failure
    import os

    if not hasattr(os, "add_dll_directory"):  # not Windows
        return
    try:
        import torch

        lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(lib):
            os.add_dll_directory(lib)
            _log.info("onnx_cuda_dlls_primed", dir=lib)
    except Exception as exc:
        _log.warning("onnx_cuda_dll_prime_failed", error=str(exc)[:200])


def _face_app():  # pragma: no cover - requires insightface + a model download
    if "face_app" not in _CACHE:
        _prime_onnx_cuda_dlls()
        from insightface.app import FaceAnalysis

        app = FaceAnalysis(
            name="buffalo_l", providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        app.prepare(ctx_id=0, det_size=(640, 640))
        _CACHE["face_app"] = app
    return _CACHE["face_app"]


def _pipeline(settings, device, dtype):  # pragma: no cover - requires diffusers + a model download
    key = ("pipe", settings.faceid_base_model)
    if key not in _CACHE:
        from diffusers import DDIMScheduler, StableDiffusionPipeline

        pipe = StableDiffusionPipeline.from_pretrained(
            settings.faceid_base_model, torch_dtype=dtype, safety_checker=None
        )
        pipe.scheduler = DDIMScheduler.from_config(pipe.scheduler.config)
        pipe.load_ip_adapter(
            settings.faceid_ip_repo,
            subfolder=None,
            weight_name=settings.faceid_ip_weight,
            image_encoder_folder=None,
        )
        pipe.set_ip_adapter_scale(float(settings.faceid_scale))
        if device == "cuda":
            pipe.enable_model_cpu_offload()  # keep peak VRAM within a ~6 GB card
        else:
            pipe.to(device)
        _CACHE[key] = pipe
    return _CACHE[key]
