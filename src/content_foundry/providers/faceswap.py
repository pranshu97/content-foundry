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
    # QUALITY: the inswapper face is only 128 px. Restore/upscale it with an offline ONNX enhancer.
    # OOM-SAFE on a 6 GB card: free the swapper's GPU memory BEFORE the restorer loads (one model at a
    # time). Best-effort — any restore problem keeps the un-restored swap.
    if getattr(settings, "thumbnail_face_restore", True):
        _release_model("swapper")
        restored = _restore_face(settings, result, target.kps)
        _release_model("restorer")
        if restored is not None:
            result = restored
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


# ------------------------------------------------------------- face restoration
# The 5-point FFHQ/512 template the ONNX restorers (GFPGAN/CodeFormer/GPEN) were trained on; aligning
# the swapped face to it makes the restoration come back correctly proportioned.
_FFHQ_512 = (
    (192.98138, 239.94708), (318.90277, 240.19366), (256.63416, 314.01935),
    (201.26117, 371.41043), (313.08905, 371.15118),
)
# Stable ONNX weights (facefusion assets on GitHub releases), tried as a direct download.
_RESTORE_URLS = {
    "gfpgan_1.4": "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/gfpgan_1.4.onnx",
    "codeformer": "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/codeformer.onnx",
    "gpen_bfr_512": "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/gpen_bfr_512.onnx",
}
# GFPGAN/CodeFormer were trained on naturally-lit faces; below this mean luma the aligned face is too
# dark / neon-cast for them and they hallucinate glassy artifacts, so the restore is skipped (keep the
# clean swap). Best-of-N scene selection makes a well-lit face the norm, so this rarely trips.
_MIN_RESTORE_LUMA = 70.0


def _release_model(key: str) -> None:
    """Drop a cached ONNX model + free its GPU memory so the NEXT model can load without OOM on a
    small card. onnxruntime releases its CUDA memory when the session object is destroyed."""
    sess = _CACHE.pop(key, None)
    if sess is not None:
        _free_gpu(sess)


def _free_gpu(sess) -> None:  # pragma: no cover - requires the GPU stack
    import gc

    del sess
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _resolve_restore_model_path(settings) -> Path:  # pragma: no cover - filesystem/network
    configured = (getattr(settings, "face_restore_model_path", "") or "").strip()
    if configured and Path(configured).exists():
        return Path(configured)
    model = (getattr(settings, "face_restore_model", "") or "gfpgan_1.4").strip()
    url = _RESTORE_URLS.get(model) or _RESTORE_URLS["gfpgan_1.4"]
    dest = Path.home() / ".insightface" / "models" / f"{model}.onnx"
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    import httpx

    _log.info("face_restore_downloading", model=model, url=url)
    with httpx.stream("GET", url, follow_redirects=True, timeout=180) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(".part")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    tmp.replace(dest)
    return dest


def _restorer_session(settings):  # pragma: no cover - requires the model file / GPU
    if "restorer" not in _CACHE:
        import onnxruntime

        from .faceid import _prime_onnx_cuda_dlls

        _prime_onnx_cuda_dlls()
        _CACHE["restorer"] = onnxruntime.InferenceSession(
            str(_resolve_restore_model_path(settings)),
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
    return _CACHE["restorer"]


def _restore_face(settings, image, kps):  # pragma: no cover - requires the model file / GPU
    """Align the swapped face to the FFHQ/512 template the ONNX restorers were trained on, run the
    restorer, and blend the sharp crop back through a soft-edged BOX mask (the facefusion method).
    Returns the enhanced full image, or ``None`` on ANY problem so the caller keeps the un-restored
    swap (never a regression).

    Alignment uses RANSAC with a generous reprojection threshold, NOT LMEDS: on just five landmarks
    LMEDS can return a degenerate transform whose paste-back SHATTERS the face across the frame. The
    blend uses a box mask feathered well INSIDE the 512 crop so the crop's rectangular edge (the hair /
    background boundary) never seams or red-streaks into the picture."""
    try:
        import cv2
        import numpy as np

        template = np.array(_FFHQ_512, dtype=np.float32)
        matrix, _ = cv2.estimateAffinePartial2D(
            np.asarray(kps, dtype=np.float32), template,
            method=cv2.RANSAC, ransacReprojThreshold=100.0,
        )
        if matrix is None:
            return None
        aligned = cv2.warpAffine(
            image, matrix, (512, 512), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_AREA
        )
        # GATE: on a very DARK or heavy-neon face (blue/red gels read LOW in luma) the restorer melts
        # into glassy artifacts. Skip it and keep the clean (if soft) swap when the crop is too dark.
        luma = float(
            0.114 * aligned[:, :, 0].mean()
            + 0.587 * aligned[:, :, 1].mean()
            + 0.299 * aligned[:, :, 2].mean()
        )
        if luma < _MIN_RESTORE_LUMA:
            _log.info("face_restore_skipped_dark", luma=round(luma, 1))
            return None
        inp = aligned[:, :, ::-1].astype(np.float32) / 255.0  # BGR->RGB, [0,1]
        inp = ((inp - 0.5) / 0.5).transpose(2, 0, 1)[None]  # ->[-1,1], NCHW
        sess = _restorer_session(settings)
        feeds = {sess.get_inputs()[0].name: inp}
        for extra in sess.get_inputs()[1:]:  # CodeFormer takes a fidelity weight as a 2nd input
            feeds[extra.name] = np.array([1.0], dtype=np.float64)
        out = sess.run(None, feeds)[0][0]
        out = np.clip(out, -1, 1)
        out = ((out + 1) / 2 * 255).transpose(1, 2, 0)[:, :, ::-1]  # ->HWC, RGB->BGR, [0,255]
        out = np.clip(out, 0, 255).astype(np.uint8)
        # Soft ELLIPSE mask over the central face (eyes / nose / mouth / cheeks) where the restorer
        # actually improves detail, feathered to 0 WELL before the hairline, ears, and jaw. A square
        # box seamed on the forehead (the restored hairline's brightness never matches the original);
        # an oval that fades out before the hairline keeps only the clean win and drops the seam.
        mask = np.zeros((512, 512), dtype=np.float32)
        cv2.ellipse(mask, (256, 322), (148, 158), 0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=30)
        height, width = image.shape[:2]
        inverse = cv2.invertAffineTransform(matrix)
        pasted = cv2.warpAffine(out, inverse, (width, height), borderMode=cv2.BORDER_REPLICATE)
        soft = cv2.warpAffine(mask, inverse, (width, height)).clip(0.0, 1.0)[..., None]
        blended = pasted.astype(np.float32) * soft + image.astype(np.float32) * (1.0 - soft)
        return blended.astype(np.uint8)
    except Exception as exc:
        _log.warning("face_restore_failed", error=str(exc)[:200])
        return None
