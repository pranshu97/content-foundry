"""Unit: creator-supplied ``--data`` — resolving text-or-file and turning it into citable facts."""

from __future__ import annotations

import pytest

from content_foundry.errors import ConfigError
from content_foundry.pipeline.creator_data import (
    MAX_ITEMS,
    creator_key_facts,
    resolve_data,
    split_source,
)


def test_resolve_data_reads_a_file_and_passes_literal_text_through(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("We shipped 3 features\nChurn fell 12%\n", encoding="utf-8")
    assert resolve_data(str(src)) == "We shipped 3 features\nChurn fell 12%"
    # Anything that isn't a real file is used AS-IS.
    assert resolve_data("Churn fell 12% last quarter") == "Churn fell 12% last quarter"
    # Multi-line input is unambiguously literal, never probed as a path.
    assert resolve_data("line one\nline two") == "line one\nline two"
    assert resolve_data(None) == "" and resolve_data("   ") == ""


def test_resolve_data_rejects_a_missing_file_instead_of_treating_it_as_a_fact(tmp_path):
    """A typo'd path must fail loudly — silently grounding the video in the string 'notes.txt'
    would be far worse than an error."""
    with pytest.raises(ConfigError):
        resolve_data(str(tmp_path / "does_not_exist.txt"))


def test_creator_key_facts_splits_lines_and_marks_them_creator_supplied():
    facts = creator_key_facts("Our churn fell 12%\n\n- Referrals drive 40% of hires\n2. NPS hit 61")
    assert [f.statement for f in facts] == [
        "Our churn fell 12%",
        "Referrals drive 40% of hires",  # bullet marker stripped
        "NPS hit 61",  # numbered marker stripped
    ]
    # The flag (NOT a fabricated citation source) is what earns these the extra weight.
    assert all(f.creator_supplied for f in facts)
    assert all(f.citation.source == "" for f in facts)  # none was given, so none is invented
    assert facts[0].citation.snippet == "Our churn fell 12%"


@pytest.mark.parametrize(
    ("line", "statement", "source"),
    [
        ("Churn fell 12% | Q3 board deck", "Churn fell 12%", "Q3 board deck"),
        ("Churn fell 12% | source: Q3 deck", "Churn fell 12%", "Q3 deck"),
        ("Churn fell 12% (source: ATS export)", "Churn fell 12%", "ATS export"),
        ("Churn fell 12% [Src=ATS export]", "Churn fell 12%", "ATS export"),
        ("Churn fell 12% -- via Q3 deck", "Churn fell 12%", "Q3 deck"),
        ("Churn fell 12%", "Churn fell 12%", ""),
        # An ordinary sentence that merely CONTAINS 'source:' must not be split.
        ("Our main source: referrals", "Our main source: referrals", ""),
    ],
)
def test_split_source_only_splits_on_a_real_separator(line, statement, source):
    assert split_source(line) == (statement, source)


def test_creator_key_facts_keeps_the_source_the_creator_gave():
    facts = creator_key_facts(
        "Churn fell 12% | Q3 board deck\nOffers rose 4% -- source: https://example.com/report"
    )
    assert facts[0].citation.source == "Q3 board deck" and facts[0].citation.url is None
    # A URL source doubles as the citation url, so the on-screen stamp can show the domain.
    assert facts[1].citation.url == "https://example.com/report"
    assert facts[1].statement == "Offers rose 4%"


def test_creator_key_facts_keeps_a_leading_negative_number_intact():
    """The bullet regex needs trailing whitespace, else '-5% churn' loses its sign."""
    assert creator_key_facts("-5% churn this quarter")[0].statement == "-5% churn this quarter"


def test_creator_key_facts_caps_a_huge_paste():
    """--data is raw operator input; an uncapped file would overflow the model's context."""
    facts = creator_key_facts("\n".join(f"fact {i}" for i in range(MAX_ITEMS * 3)))
    assert len(facts) == MAX_ITEMS
    assert creator_key_facts("") == [] and creator_key_facts("   \n\n  ") == []


def test_source_label_skips_missing_sources_and_preserves_the_creators_wording():
    """A sourceless point must not stamp 'Source: Source', and .title() must not wreck acronyms."""
    from content_foundry.agents.script_generator import _source_label

    none_given, with_source, url_source = creator_key_facts(
        "Time-to-hire fell to 21 days\n"
        "Churn fell 12% | internal ATS export\n"
        "Offers rose 4% | https://example.com/report"
    )
    assert _source_label(none_given.citation) == ""  # caller skips the stamp entirely
    assert _source_label(with_source.citation) == "internal ATS export"  # verbatim, not "Ats"
    assert _source_label(url_source.citation) == "example.com"  # domain reads better on screen
