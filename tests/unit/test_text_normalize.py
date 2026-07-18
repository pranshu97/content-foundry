"""Unit: number-to-words normalization for TTS (fixes a voice mis-reading '202,000')."""

from __future__ import annotations

from content_foundry.providers.text_normalize import speechify_numbers


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
