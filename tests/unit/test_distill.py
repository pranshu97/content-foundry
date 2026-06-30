"""Unit: deterministic distillation — every fact value comes from a real signal (Ch. 7, test #9)."""

from __future__ import annotations

from career_engine.agents import distill


def test_key_facts_copy_signal_values(sample_signals):
    facts = distill.build_key_facts(sample_signals)
    assert len(facts) == len(sample_signals)
    for fact, signal in zip(facts, sample_signals, strict=True):
        assert fact.value == signal.value  # values are copied, never invented
        assert fact.metric == signal.kind
        assert fact.citation.source == signal.source
        assert fact.citation.snippet  # non-empty, derived from the signal


def test_angles_reference_real_facts(sample_signals):
    angles = distill.build_angles(sample_signals)
    assert len(angles) >= 2
    for angle in angles:
        assert angle.supporting_fact_ids
        assert angle.hook
        assert angle.why_nonobvious


def test_layoff_statement_uses_title(sample_signals):
    layoff_signal = next(s for s in sample_signals if s.kind == "layoff")
    fact = distill.build_key_fact(layoff_signal)
    assert layoff_signal.title in fact.statement
