"""Unit: number-to-words normalization for TTS (fixes a voice mis-reading '202,000')."""

from __future__ import annotations

import itertools

import pytest

from content_foundry.providers.text_normalize import (
    is_curated,
    speechify_numbers,
    spell_designations,
    spell_initialisms,
)


def test_expands_comma_grouped_numbers():
    out = speechify_numbers("MLEs pull 202,000 dollars, SWEs trail at 160,000.")
    assert "two hundred and two thousand dollars" in out
    assert "one hundred and sixty thousand" in out
    assert "202,000" not in out and "160,000" not in out


def test_expands_currency_and_scale_suffixes():
    assert speechify_numbers("$202K") == "two hundred and two thousand dollars"
    assert "million" in speechify_numbers("$1.5M")  # one million, five hundred thousand dollars


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        # The glued suffix always worked; it is the SPACED scale word that broke.
        ("$1M", "one million dollars"),
        ("$1 million", "one million dollars"),
        ("$1 billion", "one billion dollars"),
        ("$2.5 billion", "two point five billion dollars"),
        ("$400 thousand", "four hundred thousand dollars"),
        # A dead decimal would otherwise be read out: "one point zero million".
        ("$1.0 million", "one million dollars"),
        # Exactly one dollar takes the singular; a scaled amount never does.
        ("$1", "one dollar"),
    ],
)
def test_a_currency_amount_never_strands_its_scale_word(raw, spoken):
    """Run 0031 opened by saying "one dollars million".

    ``_CURRENCY`` matched only the digits, appended the unit, and left the scale word behind it --
    so every "$1 million" in the script was voiced as "one dollars million". In the first fifteen
    seconds of a video about compensation, that is the number the whole premise rests on.
    """
    assert speechify_numbers(raw) == spoken


def test_currency_scale_words_survive_inside_a_sentence():
    out = speechify_numbers("a $1 million deal and a $2.5 billion market")
    assert "one million dollars" in out and "two point five billion dollars" in out
    assert "dollars million" not in out and "dollars billion" not in out


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
        ("L5", "L five"),
        ("L3", "L three"),
        ("E3", "E three"),  # Meta levels
        ("L1", "L one"),  # L1 vs L2 regularization
        ("P99", "P ninety-nine"),  # latency budgets
        ("SDE2", "S D E two"),  # Amazon titles
        ("H100", "H one hundred"),  # GPUs
        ("Q4", "Q four"),
        ("MP3", "M P three"),
        ("x86", "X eighty-six"),  # a lone lowercase letter is still a designation
    ],
)
def test_letter_number_designations_are_said_as_words(raw, spoken):
    """THE BUG WAS OURS, NOT THE VOICE'S: the plain-number pass rewrote only the DIGITS of 'L5' and
    glued the result to the letter, producing 'Lfive' - which a neural voice read as 'lee-five'. This
    niche is full of these (Google L5, Meta E3, Amazon SDE2, P99, H100).

    The LETTER is spaced and the NUMBER is worded. Spacing matches ``spell_letters``; this pass used
    to respell the letter ("ess dee ee two") on reasoning inherited from Chatterbox, and an ear test
    on IndexTTS-2 overturned it. The number still becomes words because digits are the one thing the
    voice cannot be trusted with - that was the original bug.
    """
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
    assert "L three" in out and "E three" in out and "L four" in out
    assert "two hundred and forty-five thousand dollars" in out
    assert "one point five times" in out
    assert not any(bad in out for bad in ("Lthree", "Ethree", "Lfour"))


# ----------------------------------------------------- pure-letter acronyms (no digits to anchor on)
@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("SLA", "S L A"),  # the reported bug: voiced as the word "Sla"
        ("ETL", "E T L"),
        # AI is pinned UNSPACED: "A I" tokenizes to the article + the pronoun, so the voice read it
        # as "an applied eye scientist" in run 0036. Unspaced it is one indivisible piece.
        ("AI", "AI"),
        ("GPU", "G P U"),
        ("KPI", "K P I"),
        # Plurals: a person says the LAST letter pluralised, never a trailing "ess". Only the tail
        # gets a phonetic spelling, because only the tail carries a suffix.
        ("APIs", "A P eyes"),
        ("SLAs", "S L ays"),
        ("GPUs", "G P yous"),
    ],
)
def test_initialisms_are_spaced_not_phonetically_respelled(raw, spoken):
    """Spaced capitals ask the voice for the LETTER; a respelling asks it to read an invented word.

    We shipped the respelling and it lost twice. "ay" for the letter A reads as the interjection
    *aye*, so ``AI`` came out "eye eye" -- across 61 occurrences of the channel's most common
    acronym before it was caught -- and the same trap made MLE "E M L E".
    """
    assert spell_initialisms(raw) == spoken


def test_no_letter_is_rendered_as_an_invented_word():
    """The property behind the parametrize table: nothing but capitals, spaces and a plural tail.

    Locks the whole class shut rather than the eight examples above, so a future well-meaning
    'let me just respell this one letter' cannot slip back in.
    """
    import string

    for a, b in itertools.product(string.ascii_uppercase, repeat=2):
        word = a + b
        if is_curated(word):
            continue
        assert spell_initialisms(word) == f"{a} {b}"


@pytest.mark.parametrize(
    "word", ["FAANG", "NASA", "JSON", "YAML", "REST", "CRUD", "CUDA", "BERT", "RAG", "LASER"]
)
def test_acronyms_that_are_real_words_are_left_alone(word):
    """Spelling these would BE the bug. FAANG is the one that matters: it is in nearly every video on
    this channel and is said "fang", so "eff ay ay en gee" would be unlistenable."""
    assert spell_initialisms(f"a {word} thing") == f"a {word} thing"


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        # Part letter, part word -- never spelled straight through.
        ("VRAM", "vee ram"),
        # All-vowel-initial letter names smear into one word ("em el ee" -> "emelee"), which is what
        # the operator heard as "E M L E". Consonant onsets do not need this (see API/GPU above).
        # Spaced bare capitals, NOT phonetic spellings: the written-out name "em" was itself read as
        # a leading letter, which is where the spurious "E" in "E M L E" came from.
        ("MLE", "M L E"),
        ("MLEs", "M L ees"),
        ("ML", "M L"),
        ("LLM", "L L M"),
    ],
)
def test_acronyms_the_default_rule_gets_wrong_are_overridden(raw, spoken):
    assert spell_initialisms(raw) == spoken


def test_an_override_is_not_re_spelled_by_the_same_pass():
    """The overrides now EMIT capitals ("M L E"), so a second pass over the output would be a bug --
    "M L" must not come back round as an initialism and turn into "em el"."""
    assert spell_initialisms(spell_initialisms("MLE")) == "M L E"
    assert spell_initialisms("The MLE and the LLM") == "The M L E and the L L M"


def test_an_override_beats_the_word_list_and_the_default():
    """The override map is the operator's escape hatch, so it has to win over both other paths."""
    out = spell_initialisms("The MLE checked VRAM on the RAG stack.")
    assert "M L E" in out  # override applied
    assert "vee ram" in out  # override applied
    assert "RAG" in out  # still exempt as a real word
    assert "vee ar ay em" not in out  # the wrong default is gone


def test_initialisms_leave_ordinary_prose_untouched():
    # Single letters are never spelled -- "I" and "A" are words.
    assert spell_initialisms("I think A is fine") == "I think A is fine"
    assert spell_initialisms("lowercase api and sla") == "lowercase api and sla"
    assert spell_initialisms("") == ""


def test_a_designation_is_not_spelled_twice():
    """``spell_designations`` runs first and turns SDE2 into "ess dee ee two"; the initialism pass
    must not then find a fresh capital run to re-spell, and must not chew the letters off a token it
    has no business touching.

    NOTE the two rules render differently ON PURPOSE. A designation still spells its letter ("ess dee
    ee two") because it has to sit against a NUMBER, and "SDE 2" would leave the voice guessing where
    the token ends. That form was verified working across ~30 uses and is deliberately not disturbed.
    """
    out = speechify_numbers("An SDE2 owns the SLA for that P99 budget.")
    assert "S D E two" in out
    assert "S L A" in out  # SLA spaced
    assert "P ninety-nine" in out
    # No doubled spelling artefacts.
    assert "ess ess" not in out and "ee ee ee" not in out


@pytest.mark.parametrize(
    ("raw", "spoken", "forbidden"),
    [
        ("3 MP3 files", "three M P three", "million"),
        ("202,000 M5 units", "two hundred and two thousand M five", "million"),
        ("5 B2 slots", "five B two", "billion"),
        ("12 K9 units", "twelve K nine", "thousand"),
    ],
)
def test_a_number_before_a_designation_is_not_read_as_a_scale_suffix(raw, spoken, forbidden):
    """Designations emit CAPITALS now, and _SCALED reads a capital M/K/B after a number as a
    million/thousand/billion suffix. That makes the pass order load-bearing: run designations before
    _SCALED and "3 MP3" becomes "three million P three". Same trap that pushed spell_initialisms to
    the end of the pipeline.
    """
    out = speechify_numbers(raw)
    assert spoken in out
    assert forbidden not in out


def test_a_real_narration_line_reads_cleanly():
    """The exact shape that produced the complaint, end to end through the TTS pass."""
    out = speechify_numbers("Our SLA is a 20ms p99, enforced by the ML platform team at FAANG.")
    assert "S L A" in out
    assert "FAANG" in out  # survives
    assert "M L platform" in out  # ML, via the override that stops the vowel smear
    assert "twenty milliseconds" in out  # NOT "twentyms"
    assert "SLA" not in out


def test_a_number_before_an_acronym_is_not_read_as_a_scale_suffix():
    """ "3 MLEs" must not become "three million L ees".

    The overrides emit bare capitals, and ``_SCALED`` reads a capital M/K/B after a number as a
    million/thousand/billion suffix. That makes the pass ORDER load-bearing: initialisms have to run
    after every number is already a word, or the M we just wrote gets eaten as a multiplier.
    """
    out = speechify_numbers("We hired 3 MLEs and 2 MLOps engineers.")
    assert "three M L ees" in out
    assert "million" not in out
    assert "two MLOps" in out  # a real word, left alone


def test_a_unit_is_not_hidden_by_the_acronym_pass():
    """ "16 GB" is a unit, not an initialism -- spelling it first would strand "jee bee"."""
    out = speechify_numbers("The box has 16 GB of VRAM and a 40GB card.")
    assert "sixteen gigabytes" in out
    assert "forty gigabytes" in out
    assert "vee ram" in out
    assert "jee bee" not in out


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("20ms", "twenty milliseconds"),
        ("1ms", "one millisecond"),  # singular, not "one milliseconds"
        ("40 ms", "forty milliseconds"),
        ("80GB", "eighty gigabytes"),
        ("3.5GHz", "three point five gigahertzs"),
    ],
)
def test_a_unit_suffix_is_never_glued_to_the_spelled_number(raw, spoken):
    """ "20ms" came out as "twentyms": the plain-number pass expanded the digits and stranded the
    suffix, the same failure shape as "L5" -> "Lfive". Latency budgets are core to this niche."""
    assert speechify_numbers(raw) == spoken
