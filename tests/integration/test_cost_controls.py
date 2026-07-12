"""Integration: cost controls — hard budget cap, opt-in fail-fast, resume artifact reuse."""

from __future__ import annotations

import pytest

from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.errors import BudgetExhaustedError
from content_foundry.models import RunState, Verdict
from content_foundry.notifications import NullNotifier
from content_foundry.pipeline.orchestrator import Orchestrator
from content_foundry.providers.youtube import DryRunPublisher

_LOW_JUDGE = {
    "actionability": {"justification": "weak", "evidence": "x", "score_1_5": 1},
    "insight": {"justification": "cliche", "evidence": "x", "score_1_5": 1},
}


def _settings(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    reset_settings_cache()
    return get_settings()


def test_budget_cap_aborts_before_spend(monkeypatch, repo, sample_signals, fakes):
    settings = _settings(monkeypatch, MONTHLY_BUDGET_USD="0.01")  # cap enforced by default
    repo.set_meta("spend_month", "5.0")  # already over budget
    llm = fakes.LLM()
    orch = Orchestrator(
        settings, repository=repo, notifier=NullNotifier(), llm_provider=llm,
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    with pytest.raises(BudgetExhaustedError):
        orch.run(from_stage="fetch", to_stage="judge", niche="tech", run_id="BUDGET")
    assert llm.call_count == 0  # never paid for generation
    assert repo.get_run("BUDGET").state == RunState.FAILED.value


def test_budget_cap_can_be_disabled(monkeypatch, repo, sample_signals, fakes):
    settings = _settings(monkeypatch, MONTHLY_BUDGET_USD="0.01", ENFORCE_BUDGET_CAP="false")
    repo.set_meta("spend_month", "5.0")
    llm = fakes.LLM()
    orch = Orchestrator(
        settings, repository=repo, notifier=NullNotifier(), llm_provider=llm,
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    result = orch.run(from_stage="fetch", to_stage="judge", niche="tech", run_id="OK")
    assert llm.call_count >= 1  # ran despite being over budget
    assert result.verdict == Verdict.PASS


def test_fail_fast_aborts_revision_loop(monkeypatch, repo, sample_signals, fakes, generic_payload):
    settings = _settings(monkeypatch, FAIL_FAST_SCORE="4.95", MAX_REVISIONS="3")
    llm = fakes.LLM(script_json=generic_payload, judge_json=_LOW_JUDGE)
    orch = Orchestrator(
        settings, repository=repo, notifier=NullNotifier(), llm_provider=llm,
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    result = orch.run(from_stage="fetch", to_stage="judge", niche="tech", run_id="FF")
    assert result.final_state == RunState.FAILED
    assert len(repo.get_attempts("FF")) == 2  # aborted at attempt 2, not MAX_REVISIONS=3


def test_fail_fast_disabled_by_default(monkeypatch, repo, sample_signals, fakes, generic_payload):
    settings = _settings(monkeypatch, MAX_REVISIONS="3")  # FAIL_FAST_SCORE defaults to 0 (off)
    llm = fakes.LLM(script_json=generic_payload, judge_json=_LOW_JUDGE)
    orch = Orchestrator(
        settings, repository=repo, notifier=NullNotifier(), llm_provider=llm,
        sources=[fakes.DataSource("adzuna", sample_signals)],
    )
    orch.run(from_stage="fetch", to_stage="judge", niche="tech", run_id="NOFF")
    assert len(repo.get_attempts("NOFF")) == 3  # runs the full loop (contract preserved)


def _producer(settings, fakes, tts, **kw):
    return Orchestrator(
        settings, notifier=NullNotifier(), dry_run=True,
        tts_provider=tts, image_provider=None, render_backend=fakes.Render(),
        publisher=DryRunPublisher(), **kw,
    )


def test_resume_reuses_paid_artifacts(monkeypatch, sample_signals, fakes):
    settings = _settings(monkeypatch)  # defaults; shared tmp DB + output dir
    # Phase 1 — produce a voiceover artifact (pays for TTS once).
    tts1 = fakes.TTS(with_timings=True)
    orch1 = _producer(settings, fakes, tts1, llm_provider=fakes.LLM(),
                      sources=[fakes.DataSource("adzuna", sample_signals)])
    r1 = orch1.run(from_stage="fetch", to_stage="voiceover", niche="tech", run_id="RESUME")
    assert r1.final_state == RunState.VOICED
    assert tts1.calls >= 1

    # Phase 2 — resume the SAME stage: the existing voiceover.json must be reused (no new spend).
    tts2 = fakes.TTS(with_timings=True)
    _producer(settings, fakes, tts2).run(
        run_id="RESUME", from_stage="voiceover", to_stage="voiceover"
    )
    assert tts2.calls == 0

    # Phase 3 — force=True bypasses the skip and regenerates.
    tts3 = fakes.TTS(with_timings=True)
    _producer(settings, fakes, tts3).run(
        run_id="RESUME", from_stage="voiceover", to_stage="voiceover", force=True
    )
    assert tts3.calls >= 1
