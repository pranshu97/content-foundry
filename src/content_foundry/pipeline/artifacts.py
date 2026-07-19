"""Artifact load/save/validate + run-directory layout (Ch. 4.3, 19)."""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..errors import SchemaValidationError

T = TypeVar("T", bound=BaseModel)

SUPPORTED_SCHEMA_VERSIONS = {"1.0"}

ARTIFACT_FILENAMES = {
    "data_brief": "data_brief.json",
    "script": "script.json",
    "judge_report": "judge_report.json",
    "voiceover": "voiceover.json",
    "visuals": "visuals.json",
    "video": "video.json",
    "publish": "publish_result.json",
}


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    root: Path
    assets: Path
    scenes: Path

    def artifact(self, stage: str) -> Path:
        return self.root / ARTIFACT_FILENAMES[stage]

    @property
    def package(self) -> Path:
        return self.root / "package.md"

    @property
    def ideas(self) -> Path:
        """Sidecar recording the brainstormed ideas + the exact pick (not a pipeline stage)."""
        return self.root / "ideas.json"

    @property
    def research(self) -> Path:
        """Sidecar research report from Agent 1.5 (not a pipeline stage)."""
        return self.root / "research.json"

    @property
    def meta(self) -> Path:
        """Sidecar of run-level facts (e.g. content_format) so a re-run keeps the run's shape."""
        return self.root / "run_meta.json"

    @property
    def end_screen(self) -> Path:
        """Sidecar: the two topically-related prior videos (name + link) for the manual end screen."""
        return self.root / "end_screen.json"


def run_paths(run_id: str, output_dir: str) -> RunPaths:
    root = Path(output_dir) / run_id
    return RunPaths(run_id=run_id, root=root, assets=root / "assets", scenes=root / "assets" / "scenes")


def next_run_id(output_dir: str) -> str:
    """Next sequential, zero-padded run id (e.g. "0006") — a simple continuation of the highest
    numbered run folder under ``output_dir``. Legacy non-numeric folders (old ULIDs) are ignored; an
    empty or missing folder starts at "0001". Short and easy to type when resuming."""
    root = Path(output_dir)
    highest = 0
    if root.exists():
        for child in root.iterdir():
            if child.is_dir() and child.name.isdigit():
                highest = max(highest, int(child.name))
    return f"{highest + 1:04d}"


def ensure_run_dirs(paths: RunPaths) -> None:
    paths.scenes.mkdir(parents=True, exist_ok=True)


def save_run_format(paths: RunPaths, content_format: str) -> None:
    """Persist the run's content_format ('long'/'short') so a later re-run or thumbnail refinement
    stays in the SAME shape regardless of the current CONTENT_FORMAT default. Best-effort."""
    with contextlib.suppress(OSError):
        paths.meta.write_text(
            json.dumps({"content_format": content_format}, indent=2), encoding="utf-8"
        )


def load_run_format(run_id: str, output_dir: str) -> str | None:
    """The persisted content_format for an EXISTING run ('long'/'short'), or ``None`` when unknown.
    Falls back to the rendered video's resolution for runs made before ``run_meta.json`` existed
    (portrait => short)."""
    paths = run_paths(run_id, output_dir)
    if paths.meta.exists():
        try:
            fmt = json.loads(paths.meta.read_text(encoding="utf-8")).get("content_format")
            if fmt in ("long", "short"):
                return fmt
        except (json.JSONDecodeError, OSError):
            pass
    video = paths.artifact("video")  # older runs: infer from the rendered resolution
    if video.exists():
        try:
            res = str(json.loads(video.read_text(encoding="utf-8")).get("resolution", ""))
            w, _, h = res.partition("x")
            if w.isdigit() and h.isdigit():
                return "short" if int(h) > int(w) else "long"
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return None


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def save_model(model: BaseModel, path: str | Path) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return str(target)


def load_model(model_cls: type[T], path: str | Path, *, expected_stage: str | None = None) -> T:
    """Load + validate a JSON artifact, checking ``schema_version`` and (optionally) ``stage``."""
    raw_text = Path(path).read_text(encoding="utf-8")
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(f"{path}: invalid JSON ({exc})") from exc

    version = raw.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SchemaValidationError(
            f"{path}: unsupported schema_version {version!r}; supported={sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    if expected_stage is not None and raw.get("stage") != expected_stage:
        raise SchemaValidationError(
            f"{path}: expected stage {expected_stage!r}, got {raw.get('stage')!r}"
        )
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        raise SchemaValidationError(f"{path}: does not match {model_cls.__name__}: {exc}") from exc
