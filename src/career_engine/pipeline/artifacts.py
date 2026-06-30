"""Artifact load/save/validate + run-directory layout (Ch. 4.3, 19)."""

from __future__ import annotations

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


def run_paths(run_id: str, output_dir: str) -> RunPaths:
    root = Path(output_dir) / run_id
    return RunPaths(run_id=run_id, root=root, assets=root / "assets", scenes=root / "assets" / "scenes")


def ensure_run_dirs(paths: RunPaths) -> None:
    paths.scenes.mkdir(parents=True, exist_ok=True)


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
