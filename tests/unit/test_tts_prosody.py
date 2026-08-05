"""Pause shaping + cloning-reference window selection (Ch. 10.5)."""

from __future__ import annotations

import pytest

from content_foundry.providers.tts import (
    _chunk_for_tts,
    _keep_slices,
    best_reference_window,
    pause_after_ms,
)


class TestPauseAfterMs:
    def test_sentence_end_gets_the_full_base_pause(self):
        assert pause_after_ms("That line is an instant auto-reject.", base=300) == 300

    def test_question_gets_more_air_than_a_statement(self):
        assert pause_after_ms("Notice what happened there?", base=300) > pause_after_ms(
            "Notice what happened there.", base=300
        )

    def test_mid_sentence_split_is_nearly_continuous(self):
        """The killer case: a long sentence force-split on a word boundary. A full sentence pause
        here lands as a stutter in the middle of a clause."""
        mid = pause_after_ms("you have never actually built a real", base=300)
        assert mid < pause_after_ms("you have never built one.", base=300) / 3
        assert mid == 75

    def test_comma_and_colon_sit_between_the_two(self):
        assert pause_after_ms("first, ", base=300) == 150
        assert pause_after_ms("consider this:", base=300) == 210
        assert pause_after_ms("one thing;", base=300) == 210

    def test_only_the_FINAL_mark_counts(self):
        """Punctuation in the middle of a chunk is the model's business; only the mark the chunk
        ENDS on decides the gap before the next chunk starts."""
        assert pause_after_ms("one thing; another", base=300) == 75
        assert pause_after_ms("Yes? No.", base=300) == 300

    def test_looks_past_closing_quotes_and_brackets(self):
        assert pause_after_ms('he called it "a glorified script."', base=300) == 300
        assert pause_after_ms("(the recruiter never saw it!)", base=300) == 375

    def test_scales_with_base_and_never_goes_negative(self):
        assert pause_after_ms("done.", base=0) == 0
        assert pause_after_ms("done.", base=-50) == 0
        assert pause_after_ms("done.", base=600) == 600

    def test_empty_and_whitespace_are_safe(self):
        assert pause_after_ms("", base=300) == 75
        assert pause_after_ms("   ", base=300) == 75

    def test_real_narration_boundaries_are_not_all_equal(self):
        """Regression guard for the actual defect: every join used to get an identical gap."""
        text = (
            "The real filter is a specialist screener. They spot a framework operator in five "
            "seconds flat. Notice what happened there? You listed the tools, but you never built "
            "a system."
        )
        gaps = {pause_after_ms(c, base=300) for c in _chunk_for_tts(text, max_chars=60)[:-1]}
        assert len(gaps) > 1


class TestBestReferenceWindow:
    def test_skips_leading_silence(self):
        """The measured bug: 0.81 s of silence sat inside the 6 s Chatterbox uses for prosody."""
        voiced = [False] * 20 + [True] * 40
        assert best_reference_window(voiced, window=30) == (20, 50)

    def test_picks_the_densest_window_not_the_first(self):
        voiced = [True, False] * 15 + [True] * 20  # sparse first, dense later
        start, end = best_reference_window(voiced, window=20)
        assert (start, end) == (30, 50)
        assert all(voiced[start:end])

    def test_ties_keep_the_earliest_window(self):
        voiced = [True] * 10 + [False] * 5 + [True] * 10
        assert best_reference_window(voiced, window=10) == (0, 10)

    def test_short_or_disabled_inputs_return_everything(self):
        voiced = [True] * 8
        assert best_reference_window(voiced, window=20) == (0, 8)
        assert best_reference_window(voiced, window=8) == (0, 8)
        assert best_reference_window(voiced, window=0) == (0, 8)
        assert best_reference_window([], window=5) == (0, 0)

    def test_window_length_is_exact(self):
        voiced = [True, False, True, True, False, True, True, True, False, True]
        for w in range(1, len(voiced)):
            start, end = best_reference_window(voiced, window=w)
            assert end - start == w
            assert start >= 0 and end <= len(voiced)

    def test_all_silent_input_is_still_a_valid_window(self):
        assert best_reference_window([False] * 10, window=4) == (0, 4)


class TestKeepSlicesEdgePad:
    def test_edge_pad_defaults_to_pad_so_old_callers_are_unchanged(self):
        n = 1000
        silent = [(0, 100), (300, 700), (900, 1000)]
        assert _keep_slices(n, silent, pad=10, max_gap=200) == _keep_slices(
            n, silent, pad=10, max_gap=200, edge_pad=10
        )

    def test_edges_trim_tighter_while_internal_collapse_keeps_its_own_beat(self):
        """Edges are trimmed close so the caller's explicit punctuation pause is what is heard, but a
        collapsed INTERNAL gap must still land on the natural 2*pad beat."""
        n = 1000
        silent = [(0, 100), (300, 700), (900, 1000)]
        keep = _keep_slices(n, silent, pad=50, max_gap=200, edge_pad=2)
        assert keep == [(98, 350), (650, 902)]

    def test_voiced_samples_are_never_cut(self):
        n = 1000
        silent = [(0, 100), (300, 700), (900, 1000)]
        keep = _keep_slices(n, silent, pad=10, max_gap=100, edge_pad=0)
        covered = set()
        for a, b in keep:
            covered |= set(range(a, b))
        assert set(range(100, 300)) <= covered
        assert set(range(700, 900)) <= covered


@pytest.mark.parametrize("mark", [".", "?", "!", ",", ";", ":"])
def test_every_supported_mark_yields_a_positive_pause(mark):
    assert pause_after_ms(f"a line{mark}", base=300) > 0
