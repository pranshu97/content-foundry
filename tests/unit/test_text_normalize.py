"""Unit: number-to-words normalization for TTS (fixes a voice mis-reading '202,000')."""

from __future__ import annotations

import pytest

from content_foundry.providers.text_normalize import speechify_numbers, spell_designations


def test_expands_comma_grouped_numbers():
    out = speechify_numbers("MLEs pull 202,000 dollars, SWEs trail at 160,000.")
    assert "two hundred and two thousand dollars" in out
    assert "one hundred and sixty thousand" in out
    assert "202,000" not in out and "160,000" not in out


def test_expands_currency_and_scale_suffixes():
    assert speechify_numbers("$202K") == "two hundred and two thousand dollars"
    assert "million" in speechify_numbers("$1.5M")  # one million, five hundred thousand dollars


def test_expands_percent_and_times():
    assert "one percent" in speechify_numbers("top 1%")
    assert "three times" in speechify_numbers("3x faster")


def test_plain_words_and_empty_are_unchanged():
    assert speechify_numbers("ninety days, no numbers here") == "ninety days, no numbers here"
    assert speechify_numbers("") == ""
    assert "ninety" in speechify_numbers("90 days")  # bare integers still expand


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("L5", "el five"),
        ("L3", "el three"),
        ("E3", "ee three"),  # Meta levels
        ("L1", "el one"),  # L1 vs L2 regularization
        ("P99", "pee ninety-nine"),  # latency budgets
        ("SDE2", "ess dee ee two"),  # Amazon titles
        ("H100", "aitch one hundred"),  # GPUs
        ("Q4", "cue four"),
        ("MP3", "em pee three"),
        ("x86", "ex eighty-six"),  # a lone lowercase letter is still a designation
    ],
)
def test_letter_number_designations_are_said_as_words(raw, spoken):
    """THE BUG WAS OURS, NOT THE VOICE'S: the plain-number pass rewrote only the DIGITS of 'L5' and
    glued the result to the letter, producing 'Lfive' — which a neural voice read as 'lee-five'. This
    niche is full of these (Google L5, Meta E3, Amazon SDE2, P99, H100), so every part of the output
    has to be an ordinary English word, leaving the front-end nothing to guess at."""
    out = speechify_numbers(f"promoted to {raw} last year")
    assert spoken in out
    assert raw not in out


def test_designations_do_not_swallow_ordinary_words():
    """A multi-letter LOWERCASE run before digits is usually a word, not a designation — spelling
    'top5' as 'tee oh pee five' would be far worse than the bug being fixed. Uppercase runs are also
    capped at four letters, because at five real words like 'round3' start matching."""
    assert spell_designations("my top5 picks") == "my top5 picks"
    assert spell_designations("round3 of the loop") == "round3 of the loop"
    # Ordinary prose and standalone numbers are untouched by this pass.
    assert spell_designations("90 days and 202,000 dollars") == "90 days and 202,000 dollars"
    assert spell_designations("") == ""


def test_designations_survive_the_rest_of_the_pipeline():
    """The designation pass must run BEFORE the plain-number pass, or the digits are consumed first
    and the glued token comes straight back."""
    out = speechify_numbers("A Google L3 equals a Meta E3, and $245,000 at L4 is 1.5x that.")
    assert "el three" in out and "ee three" in out and "el four" in out
    assert "two hundred and forty-five thousand dollars" in out
    assert "one point five times" in out
    assert not any(bad in out for bad in ("Lthree", "Ethree", "Lfour"))
