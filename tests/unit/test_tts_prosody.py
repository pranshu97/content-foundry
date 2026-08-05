"""Pause shaping + cloning-reference window selection (Ch. 10.5)."""

from __future__ import annotations

import pytest

from content_foundry.agents.voiceover import tone_for_script
from content_foundry.providers.tts import (
    TONE_EMOTIONS,
    TONE_WEIGHTS,
    _chunk_for_tts,
    _keep_slices,
    best_reference_window,
    pause_after_ms,
    pick_toned_window,
    tone_scores,
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


def _profile(density, dynamics, pitch, pace, pitch_hz=110.0):
    return {
        "density": density,
        "dynamics": dynamics,
        "pitch": pitch,
        "pace": pace,
        "pitch_hz": pitch_hz,
    }


class TestToneSelection:
    """Windows of ONE recording differ in delivery: measured on the real 102 s reference, pace varied
    46% and dynamics 27% while the pitch median moved only 10%."""

    def _corpus(self):
        return [
            _profile(0.73, 8.7, 45.4, 6.2),  # 0 dense + quick, flat-ish
            _profile(0.44, 9.2, 50.2, 4.5),  # 1 sparse + SLOW, wide dynamics
            _profile(0.61, 9.5, 56.3, 5.7),  # 2 wide dynamics + liveliest pitch
            _profile(0.68, 7.2, 46.0, 7.0),  # 3 fastest + dense, narrow dynamics
        ]

    def test_authoritative_picks_the_slow_wide_dynamic_window(self):
        assert pick_toned_window(self._corpus(), "authoritative") == 1

    def test_punchy_picks_the_widest_dynamics_and_liveliest_pitch(self):
        assert pick_toned_window(self._corpus(), "punchy") == 2

    def test_energetic_picks_the_fastest_densest_window(self):
        assert pick_toned_window(self._corpus(), "energetic") == 3

    def test_neutral_picks_the_densest_window_matching_the_old_behaviour(self):
        assert pick_toned_window(self._corpus(), "neutral") == 0

    def test_different_tones_really_do_pick_different_windows(self):
        picks = {t: pick_toned_window(self._corpus(), t) for t in TONE_WEIGHTS}
        assert len(set(picks.values())) == len(TONE_WEIGHTS), picks

    def test_a_pitch_outlier_is_rejected_so_the_voice_stays_recognisable(self):
        """Pitch median carries identity. A window that would win on every delivery feature is still
        dropped when its pitch says it is not the same voice."""
        corpus = [*self._corpus(), _profile(0.95, 12.0, 70.0, 9.0, pitch_hz=240.0)]
        for tone in TONE_WEIGHTS:
            assert pick_toned_window(corpus, tone) != 4

    def test_falls_back_when_every_window_looks_like_an_outlier(self):
        corpus = [_profile(0.6, 8.0, 50.0, 5.0, pitch_hz=0.0)]
        assert pick_toned_window(corpus, "punchy") == 0

    def test_unknown_tone_falls_back_to_the_neutral_baseline(self):
        corpus = self._corpus()
        assert pick_toned_window(corpus, "nonsense") == pick_toned_window(corpus, "neutral")

    def test_empty_and_single_candidate_are_safe(self):
        assert pick_toned_window([], "punchy") == 0
        assert pick_toned_window([_profile(0.5, 8.0, 50.0, 5.0)], "punchy") == 0

    def test_ties_keep_the_earliest_window(self):
        same = [_profile(0.6, 8.0, 50.0, 5.0) for _ in range(4)]
        for tone in TONE_WEIGHTS:
            assert pick_toned_window(same, tone) == 0

    def test_scoring_is_scale_invariant_across_speakers(self):
        """Features are normalised ACROSS candidates, so doubling every dynamics reading (a louder
        mic) must not change which window wins."""
        corpus = self._corpus()
        louder = [{**p, "dynamics": p["dynamics"] * 2} for p in corpus]
        for tone in TONE_WEIGHTS:
            assert pick_toned_window(corpus, tone) == pick_toned_window(louder, tone)

    def test_tone_scores_length_matches_input(self):
        assert tone_scores([], "punchy") == []
        assert len(tone_scores(self._corpus(), "punchy")) == 4


class TestIndexTTS2Config:
    """IndexTTS-2 is driven out-of-process; only the pure wiring is testable without the model."""

    def _provider(self, **kw):
        from content_foundry.providers.tts import IndexTTS2

        defaults = {
            "python_exe": "C:/index-tts/.venv/Scripts/python.exe",
            "model_dir": "C:/index-tts/checkpoints",
        }
        return IndexTTS2("ref.wav", **{**defaults, **kw})

    def test_cfg_path_defaults_under_the_model_dir(self):
        assert self._provider()._cfg.replace("\\", "/").endswith("checkpoints/config.yaml")

    def test_an_explicit_cfg_path_wins(self):
        assert self._provider(cfg_path="D:/custom.yaml")._cfg == "D:/custom.yaml"

    def test_emotion_is_off_by_default_so_the_baseline_is_a_plain_clone(self):
        p = self._provider()
        p.set_tone("punchy")
        assert p._emotion_vector() == []

    def test_auto_emotion_follows_the_tone(self):
        p = self._provider(emotion="auto")
        p.set_tone("punchy")
        assert p._emotion_vector() == TONE_EMOTIONS["punchy"]
        p.set_tone("energetic")
        assert p._emotion_vector() == TONE_EMOTIONS["energetic"]

    def test_neutral_carries_no_emotion_even_on_auto(self):
        p = self._provider(emotion="auto")
        p.set_tone("neutral")
        assert p._emotion_vector() == []

    def test_unknown_tone_is_ignored_rather_than_breaking_synthesis(self):
        p = self._provider(emotion="auto")
        p.set_tone("punchy")
        p.set_tone("nonsense")
        assert p._tone == "punchy"

    def test_every_emotion_vector_is_a_valid_eight_slot_low_intensity_vector(self):
        """The model takes [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm].
        Keep intensities modest: an explainer that emotes hard reads as a stunt."""
        for tone, vector in TONE_EMOTIONS.items():
            assert tone in TONE_WEIGHTS
            if not vector:
                continue
            assert len(vector) == 8, tone
            assert all(0.0 <= v <= 1.0 for v in vector), tone
            assert sum(vector) <= 1.0, tone

    def test_voice_label_comes_from_the_reference_clip(self):
        from content_foundry.providers.tts import IndexTTS2

        p = IndexTTS2("assets/my_voice.wav", python_exe="py.exe", model_dir="C:/checkpoints")
        assert p.voice == "my_voice"
        assert p.name == "indextts"


class TestToneForScript:
    def test_contrarian_and_myth_scripts_get_the_emphatic_delivery(self):
        assert tone_for_script("contrarian") == "punchy"
        assert tone_for_script("myth_vs_reality") == "punchy"

    def test_number_heavy_and_stepwise_scripts_get_room_to_breathe(self):
        assert tone_for_script("data_deep_dive") == "authoritative"
        assert tone_for_script("three_step") == "authoritative"

    def test_narrative_scripts_get_momentum(self):
        assert tone_for_script("case_study") == "energetic"
        assert tone_for_script("problem_solution") == "energetic"

    def test_every_mapped_tone_is_a_real_tone(self):
        for template in (
            "contrarian",
            "myth_vs_reality",
            "data_deep_dive",
            "three_step",
            "problem_solution",
            "case_study",
            "unknown",
        ):
            assert tone_for_script(template) in TONE_WEIGHTS

    def test_an_explicit_override_wins_over_the_template(self):
        assert tone_for_script("contrarian", override="authoritative") == "authoritative"

    def test_auto_and_blank_derive_from_the_template(self):
        assert tone_for_script("contrarian", override="auto") == "punchy"
        assert tone_for_script("contrarian", override="") == "punchy"

    def test_unknown_template_stays_on_the_safe_baseline(self):
        assert tone_for_script("") == "neutral"
        assert tone_for_script("something_new") == "neutral"
