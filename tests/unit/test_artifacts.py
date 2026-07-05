"""Unit: artifact save/load + schema-version validation (Ch. 19, test #7)."""

from __future__ import annotations

import json

import pytest

from content_foundry.errors import SchemaValidationError
from content_foundry.models import DataBrief
from content_foundry.pipeline.artifacts import (
    load_model,
    next_run_id,
    run_paths,
    save_model,
    sha256_file,
)


def test_next_run_id_starts_at_0001_when_empty(tmp_path):
    assert next_run_id(str(tmp_path / "runs")) == "0001"  # missing folder
    (tmp_path / "runs").mkdir()
    assert next_run_id(str(tmp_path / "runs")) == "0001"  # empty folder


def test_next_run_id_continues_from_highest(tmp_path):
    runs = tmp_path / "runs"
    for name in ("0001", "0002", "0005"):
        (runs / name).mkdir(parents=True)
    assert next_run_id(str(runs)) == "0006"  # max(5) + 1, zero-padded


def test_next_run_id_ignores_legacy_ulid_folders(tmp_path):
    runs = tmp_path / "runs"
    (runs / "01KWRZK18PFYS56YV7MHBXVJB8").mkdir(parents=True)  # old ULID run
    (runs / "0003").mkdir()
    (runs / "notes.txt").write_text("x", encoding="utf-8")  # a stray file, not a dir
    assert next_run_id(str(runs)) == "0004"



def test_save_and_load_round_trip(data_brief, tmp_path):
    path = tmp_path / "data_brief.json"
    save_model(data_brief, path)
    loaded = load_model(DataBrief, path, expected_stage="data_brief")
    assert loaded.run_id == data_brief.run_id


def test_stale_schema_version_raises(data_brief, tmp_path):
    path = tmp_path / "data_brief.json"
    raw = json.loads(data_brief.model_dump_json())
    raw["schema_version"] = "9.9"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SchemaValidationError):
        load_model(DataBrief, path, expected_stage="data_brief")


def test_wrong_stage_raises(data_brief, tmp_path):
    path = tmp_path / "data_brief.json"
    save_model(data_brief, path)
    with pytest.raises(SchemaValidationError):
        load_model(DataBrief, path, expected_stage="script")


def test_sha256_is_deterministic(data_brief, tmp_path):
    path = tmp_path / "a.json"
    save_model(data_brief, path)
    assert sha256_file(path) == sha256_file(path)
    assert sha256_file(path).startswith("sha256:")


def test_run_paths_layout(settings):
    paths = run_paths("RID", settings.output_dir)
    assert paths.artifact("script").name == "script.json"
    assert paths.assets.name == "assets"
    assert paths.package.name == "package.md"
