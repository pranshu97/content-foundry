"""Two-stage face-swap thumbnail (option B v2): generate a rich scene WITH a generic person using the
normal image provider and a LONG prompt (so the model actually follows the scene instructions — the
paths, props, and layout — with no 77-token CLIP limit), then swap the operator's REAL face onto that
person with insightface's ``inswapper``. Far better instruction-following AND identity fidelity than
SD1.5 + IP-Adapter-FaceID.

Heavy + OPTIONAL: insightface/onnxruntime/opencv are imported lazily. ANY missing dependency, a
missing swapper model, or an undetected face returns ``None``, so the caller falls back to the
composited thumbnail and the render never breaks.

ONE-TIME SETUP: the swapper weights ``inswapper_128.onnx`` (~530 MB) download automatically from a
Hugging Face mirror on first use. If that fails, download the file manually and point
``FACESWAP_MODEL_PATH`` at it (or drop it in ``~/.insightface/models/inswapper_128.onnx``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..logging import get_logger

if TYPE_CHECKING:
    from ..config import Settings

_CACHE: dict = {}
_log = get_logger(component="faceswap")
# Public Hugging Face mirrors that host inswapper_128.onnx (tried in order; the original was pulled).
_HF_REPOS = ("ezioruan/inswapper_128.onnx", "netrunner-exe/Insight-Swap-models-onnx")


def swap_face(settings: Settings, *, scene_png: bytes, face_path: str) -> bytes | None:
    """Swap the operator's face (``face_path``) onto the most prominent person in ``scene_png`` and
    return the PNG bytes, or ``None`` on any problem (missing deps/model, no face detected) so the
    caller can fall back to the composited thumbnail."""
    if not scene_png or not face_path or not Path(face_path).exists():
        return None
    try:
        return _swap(settings, scene_png=scene_png, face_path=face_path)
    except Exception as exc:  # pragma: no cover - requires the heavy GPU stack + the swapper model
        _log.warning(
            "faceswap_unavailable",
            error=str(exc)[:300],
            hint="pip install insightface onnxruntime-gpu; the inswapper model auto-downloads on "
            "first use, else set FACESWAP_MODEL_PATH to a downloaded inswapper_128.onnx",
        )
        return None


def _swap(settings, *, scene_png, face_path):  # pragma: no cover - requires models/GPU
    import cv2
    import numpy as np

    from .faceid import _face_app  # reuse the cached buffalo_l analyzer

    scene = cv2.imdecode(np.frombuffer(scene_png, dtype=np.uint8), cv2.IMREAD_COLOR)
    avatar = cv2.imread(str(face_path))
    if scene is None or avatar is None:
        return None
    app = _face_app()
    source_faces = app.get(avatar)
    scene_faces = app.get(scene)
    if not source_faces or not scene_faces:
        _log.warning("faceswap_no_face", have_source=bool(source_faces), have_scene=bool(scene_faces))
        return None
    source = max(source_faces, key=_face_area)  # the operator's face
    target = max(scene_faces, key=_face_area)  # the most prominent person in the generated scene
    result = _swapper(settings).get(scene, target, source, paste_back=True)
    ok, buf = cv2.imencode(".png", result)
    return buf.tobytes() if ok else None


def _face_area(face) -> float:
    x1, y1, x2, y2 = face.bbox
    return float(x2 - x1) * float(y2 - y1)


def _swapper(settings):  # pragma: no cover - requires the model file / network
    if "swapper" not in _CACHE:
        import insightface

        from .faceid import _prime_onnx_cuda_dlls  # put torch's CUDA 12 libs on the DLL path

        _prime_onnx_cuda_dlls()
        path = _resolve_model_path(settings)
        _CACHE["swapper"] = insightface.model_zoo.get_model(
            str(path), providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
    return _CACHE["swapper"]


def _resolve_model_path(settings) -> Path:  # pragma: no cover - filesystem/network
    """Locate ``inswapper_128.onnx``: an explicit ``FACESWAP_MODEL_PATH``, else the insightface cache,
    else a best-effort download from a public Hugging Face mirror."""
    configured = (getattr(settings, "faceswap_model_path", "") or "").strip()
    if configured and Path(configured).exists():
        return Path(configured)
    default = Path.home() / ".insightface" / "models" / "inswapper_128.onnx"
    if default.exists():
        return default
    from huggingface_hub import hf_hub_download

    for repo in _HF_REPOS:
        try:
            _log.info("faceswap_downloading", repo=repo)
            return Path(hf_hub_download(repo_id=repo, filename="inswapper_128.onnx"))
        except Exception as exc:
            _log.warning("faceswap_download_failed", repo=repo, error=str(exc)[:200])
    raise FileNotFoundError(
        "inswapper_128.onnx not found and could not be downloaded; download it manually and set "
        "FACESWAP_MODEL_PATH (or place it at ~/.insightface/models/inswapper_128.onnx)."
    )
