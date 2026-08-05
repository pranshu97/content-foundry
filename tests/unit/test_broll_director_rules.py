"""The B-roll director must know the MECHANICAL rules its queries are judged by.

These lock the prompt to the code. The director can write a perfectly relevant query and still have
it thrown away, because two downstream rules are invisible to it:

  * ``_search_terms`` keeps only the first ``_SEARCH_TERM_WORDS`` words, discriminating ones first;
  * the moment gate in ``_clip_ok`` drops any clip whose tags share no word with the sentence it
    lands under, and ``_STOCK_FILLER`` words never count as evidence.

If either constant moves, the prompt is telling the model something untrue and these fail.
"""

from __future__ import annotations

import pytest

from content_foundry.prompts import load_prompt
from content_foundry.providers.broll import _STOCK_FILLER

SYSTEM = load_prompt("broll_director.system")
LOWER = SYSTEM.lower()


def test_the_director_is_told_to_reuse_the_narrations_own_words():
    """The gate matches literal words, so a synonym is discarded however apt it is."""
    assert "synonym" in LOWER
    assert "literally appears" in LOWER
    assert "discards" in LOWER


def test_the_director_is_told_its_queries_are_laid_out_in_order():
    """Query j lands under narration slice j, which is why position matters."""
    assert "in order" in LOWER


def test_the_director_is_told_to_keep_queries_short():
    assert "3-5 words" in LOWER


@pytest.mark.parametrize(
    "word", ["office", "desk", "laptop", "computer", "business", "professional"]
)
def test_every_filler_word_the_prompt_names_is_really_ignored_by_the_matcher(word):
    """The prompt tells the model these words 'count for nothing'. That has to stay TRUE."""
    assert (
        word in _STOCK_FILLER
    ), f"prompt claims {word!r} is ignored but it is not in _STOCK_FILLER"


def test_the_short_query_advice_matches_the_real_trim_width():
    """The prompt says 3-5 words because only the first few survive `_search_terms`."""
    from content_foundry.agents.visuals import _search_terms

    long_query = "a professional business person in a modern office reviewing a resume on a laptop"
    kept = _search_terms(long_query).split()
    assert len(kept) <= 5, "prompt promises a short query survives; trim width changed"
    # and the discriminating word must be what survives, not the set dressing
    assert any(w in kept for w in ("reviewing", "resume", "person")), kept


def test_prompt_is_still_fakellm_safe_and_renders():
    """conftest's FakeLLM routes on the word 'judge'; it must never appear here."""
    assert "judge" not in LOWER
    assert "{max_queries}" in SYSTEM
    assert "{scenes_json}" in SYSTEM
