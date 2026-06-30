"""Unit: artifact/domain model validation + JSON round-trips."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from content_foundry.models import (
    DataBrief,
    JudgeReport,
    Provenance,
    Script,
    Verdict,
)


def test_data_brief_round_trip(data_brief):
    payload = data_brief.model_dump_json()
    restored = DataBrief.model_validate_json(payload)
    assert restored.stage == "data_brief"
    assert restored.run_id == data_brief.run_id
    assert len(restored.key_facts) == len(data_brief.key_facts)


def test_script_requires_provenance():
    with pytest.raises(ValidationError):
        Script(run_id="r", template_id="contrarian", hook="hi")  # missing provenance


def test_verdict_enum_values():
    assert {v.value for v in Verdict} == {"PASS", "REVISE", "FAIL"}


def test_provenance_defaults():
    p = Provenance(produced_by="x")
    assert p.schema_version == "1.0"
    assert p.created_at is not None
    assert p.input_hashes == {}


def test_judge_report_stage_literal(good_script):
    report = JudgeReport(
        run_id="r", attempt_number=1, template_id="contrarian", scores=[],
        weighted_total=8.0, insight_score=8.0, grounding_score=9.0,
        template_fatigue=False, force_shift=False, verdict=Verdict.PASS, summary="ok",
        provenance=Provenance(produced_by="judge"),
    )
    assert report.stage == "judge_report"
