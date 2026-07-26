"""Unit: artifact save/load + schema-version validation (Ch. 19, test #7)."""

from __future__ import annotations

import json

import pytest

from content_foundry.errors import SchemaValidationError
from content_foundry.models import DataBrief
from content_foundry.pipeline.artifacts import (
    load_model,
    load_run_data,
    load_run_format,
    load_run_idea,
    load_run_instructions,
    next_run_id,
    run_paths,
    save_model,
    save_run_data,
    save_run_format,
    save_run_idea,
    save_run_instructions,
    sha256_file,
)


def test_run_meta_persists_and_reuses_idea_alongside_format(tmp_path):
    out = str(tmp_path)
    paths = run_paths("0001", out)
    paths.root.mkdir(parents=True, exist_ok=True)
    # Idea and format COEXIST in the sidecar (merge, not overwrite):
    save_run_format(paths, "short")
    save_run_idea(paths, "  The ML System Design Interview  ")
    assert load_run_idea("0001", out) == "The ML System Design Interview"  # trimmed
    assert load_run_format("0001", out) == "short"  # still there after the idea write
    # Re-saving one field keeps the other:
    save_run_format(paths, "long")
    assert load_run_idea("0001", out) == "The ML System Design Interview"
    assert load_run_format("0001", out) == "long"
    # A blank idea is ignored; an unknown run has no persisted idea:
    save_run_idea(paths, "   ")
    assert load_run_idea("0001", out) == "The ML System Design Interview"
    assert load_run_idea("9999", out) is None


def test_run_meta_persists_and_reuses_instructions(tmp_path):
    out = str(tmp_path)
    paths = run_paths("0001", out)
    paths.root.mkdir(parents=True, exist_ok=True)
    # Instructions COEXIST with the idea in the sidecar (merge, not overwrite) and are trimmed:
    save_run_idea(paths, "The ML System Design Interview")
    save_run_instructions(paths, "  Focus on remote roles; keep it beginner-friendly  ")
    assert load_run_instructions("0001", out) == "Focus on remote roles; keep it beginner-friendly"
    assert load_run_idea("0001", out) == "The ML System Design Interview"  # still there
    # A blank steer is ignored; an unknown run has no persisted instructions:
    save_run_instructions(paths, "   ")
    assert load_run_instructions("0001", out) == "Focus on remote roles; keep it beginner-friendly"
    assert load_run_instructions("9999", out) is None


def test_run_meta_persists_and_reuses_creator_data(tmp_path):
    out = str(tmp_path)
    paths = run_paths("0001", out)
    paths.root.mkdir(parents=True, exist_ok=True)
    # The creator's own data COEXISTS with the idea/instructions in the sidecar, already resolved to
    # text so the run still grounds correctly if the source .txt is later edited or deleted.
    save_run_idea(paths, "The ML System Design Interview")
    save_run_data(paths, "  Our churn fell 12%\nReferrals drive 40% of hires  ")
    assert load_run_data("0001", out) == "Our churn fell 12%\nReferrals drive 40% of hires"
    assert load_run_idea("0001", out) == "The ML System Design Interview"  # still there
    # A blank is ignored; an unknown run has no persisted data:
    save_run_data(paths, "   ")
    assert load_run_data("0001", out) == "Our churn fell 12%\nReferrals drive 40% of hires"
    assert load_run_data("9999", out) is None


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
