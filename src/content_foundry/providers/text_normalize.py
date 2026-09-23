"""Speech-friendly text normalization for TTS front-ends that mispronounce raw numerals.

Chatterbox (and other local voices) read a comma-grouped figure like ``202,000`` as "two thousand"
instead of "two hundred two thousand". Expanding numbers, currency, percentages, and ``K/M/B``/``x``
suffixes into words BEFORE synthesis fixes the pronunciation. Applied only to the audio input — the
stored script narration (captions, citations) keeps the original digits.
"""

from __future__ import annotations

import re

_SCALE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

# $1.5M / 202K / $202,000 / 45% / 3x / 202,000 — matched most-specific first.
_SCALED = re.compile(r"(?P<dollar>\$)?(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<suffix>[KkMmBb])\b")
# The scale WORD has to be consumed here too. Matching only "$1" left "million" stranded behind the
# unit, so "$1 million" was voiced "one dollars million" -- caught in run 0031's first 15 seconds.
_CURRENCY = re.compile(
    r"\$(?P<num>\d[\d,]*(?:\.\d+)?)(?:\s+(?P<scale>thousand|million|billion|trillion))?\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(?P<num>\d[\d,]*(?:\.\d+)?)\s?%")
_TIMES = re.compile(r"\b(?P<num>\d[\d,]*(?:\.\d+)?)x\b")
_PLAIN = re.compile(r"\d[\d,]*(?:\.\d+)?")

# A number glued to a UNIT. Without this the plain-number pass expands only the digits and leaves the
# suffix stuck to the result -- "20ms" became "twentyms", the same failure shape as "L5" -> "Lfive".
# Latency budgets ("a 20ms p99") are everywhere in this niche, so this one is worth its own pass.
_UNIT_WORDS = {
    "ms": "millisecond",
    "kb": "kilobyte",
    "mb": "megabyte",
    "gb": "gigabyte",
    "tb": "terabyte",
    "mhz": "megahertz",
    "ghz": "gigahertz",
}
_UNIT = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s?(ms|kb|mb|gb|tb|mhz|ghz)\b", re.I)

# How each letter is SAID, so a designation becomes ordinary English words the voice cannot misread.
# Writing "L 5" instead only moves the problem: the voice still has to guess at a bare letter, and
# some read it as a word rather than a letter name. "el five" leaves nothing to guess.
_LETTER_SOUNDS = {
    "a": "ay",
    "b": "bee",
    "c": "see",
    "d": "dee",
    "e": "ee",
    "f": "eff",
    "g": "jee",
    "h": "aitch",
    "i": "eye",
    "j": "jay",
    "k": "kay",
    "l": "el",
    "m": "em",
    "n": "en",
    "o": "oh",
    "p": "pee",
    "q": "cue",
    "r": "ar",
    "s": "ess",
    "t": "tee",
    "u": "you",
    "v": "vee",
    "w": "double you",
    "x": "ex",
    "y": "why",
    "z": "zee",
}
# A letter-and-number designation: L5, E3, SDE2, P99, H100, GPT4, Q4, MP3, x86.
# UPPERCASE runs only (plus a lone lowercase letter, for p99/v2/x86) because a multi-letter LOWERCASE
# run before digits is usually an ordinary word — "top5" must not become "tee oh pee five". Capped at
# four letters for the same reason: at five, real words like "round3" and "phase2" start matching.
_DESIGNATION = re.compile(r"\b([A-Z]{1,4}|[a-z])(\d{1,3})\b")

# Acronyms whose default SPACED-CAPITALS rendering comes out wrong, mapped to what to say instead.
# What lands here is the genuine hybrid -- part letter, part word -- which no amount of spacing can
# express: VRAM is "vee ram", SWE is "swee", RBAC is "ar back", REPL is "repple".
#
# MLE/ML/LLM now render IDENTICALLY under the default rule, so their entries are redundant as
# TRANSFORMS. They stay because ``is_curated`` reads this table to decide what the pronunciation
# director may be asked about, and these three were settled by the operator's ear -- pinning them
# here is what stops a model quietly re-deciding them on some future run.
# Anything added here is said EXACTLY as written, so this is also the operator's escape hatch for any
# acronym the voice gets wrong -- no code change needed beyond the entry.
_CUSTOM_SOUNDS = {
    "VRAM": "vee ram",
    "MLE": "M L E",
    # Plurals keep the spaced letters and pluralise only the LAST one, as a person says it:
    # "M L ees", never "M L E ess".
    "MLES": "M L ees",
    "ML": "M L",
    "LLM": "L L M",
    "LLMS": "L L ems",
    # AI is the ONE acronym spacing cannot fix, and the tokenizer says why: "A I" becomes the
    # pieces _A _I, which ARE the article "a" and the pronoun "I" -- so "an Applied A I Scientist"
    # is, to the model, ordinary prose, and run 0036 duly said "an applied eye scientist". S/D/E/R/U
    # have no _X piece at all, which is the only reason S D E and R S U survive. Left unspaced, AI is
    # a single piece (_AI) that cannot decompose into words.
    "AI": "AI",
}

# Acronyms that are real WORDS when said aloud, so spelling them out would be the bug rather than
# the fix. FAANG is the one that matters most here -- it is in nearly every video on this channel and
# is said "fang"; "eff ay ay en gee" would be unlistenable.
_SPOKEN_AS_WORD = frozenset(
    {
        "FAANG", "NASA", "NATO", "LASER", "RADAR", "SCUBA", "OK",
        "JSON", "YAML", "REST", "CRUD", "SAAS", "ASCII", "CUDA", "BERT", "LORA", "RAG",
        "GAN", "GANS", "RAM", "ROM", "MLOPS", "DEVOPS", "SLAM", "ONNX",
    }
)  # fmt: skip
# An INITIALISM: a run of capitals said one letter at a time (SLA, API, ETL, LLM, GPU, KPI). The
# optional trailing lowercase "s" catches plurals -- "APIs" has no word boundary after the capitals,
# so without it the token survives intact and gets read as the word "apis".
_INITIALISM = re.compile(r"\b([A-Z]{2,6})(s?)\b")


# BUMP THIS whenever the rendering rules change -- `spell_letters`, the plural rule, `_CUSTOM_SOUNDS`
# or `_SPOKEN_AS_WORD`. A run's resolved map is cached in `assets/pronunciations.json` and is
# deliberately never re-asked, so that a hand correction sticks. The cost of that is real: after the
# spaced-capitals change, run 0030's cached map still said `AI` -> "ay eye", and re-voicing it would
# have fed the broken pronunciation straight back with a green PASS and nothing to see. Stamping the
# version lets a cache written under older rules invalidate ITSELF, while a hand edit made under the
# CURRENT rules survives every ordinary run.
RULES_VERSION = 3


def _pluralise(sound: str) -> str:
    """Pluralise a spoken form the way a person says it: "swee" -> "swees", "ess" -> "esses"."""
    return sound + ("es" if sound.endswith(("s", "x", "z")) else "s")


def spell_initialisms(text: str, overrides: dict[str, str] | None = None) -> str:
    """Say letter-by-letter acronyms as SPACED CAPITALS: ``SLA`` -> ``S L A``, ``APIs`` -> ``A P eyes``.

    The operator reported "SLA" being voiced as the word "Sla". ``spell_designations`` could not help:
    it requires a DIGIT, so pure-letter acronyms fell straight through to the voice, which then had to
    guess whether a run of capitals was a word or a spelling. Some it guesses right (AI), some it
    mangles (SLA, ETL, LLM).

    A blanket rule would be worse than the bug, because some capital runs ARE words -- see
    ``_SPOKEN_AS_WORD``. Everything else is spaced, which is the safe default: spaced capitals are
    always intelligible, whereas a mis-guessed word is not. See ``spell_letters`` for why the spacing
    replaced the phonetic respelling this function used to emit.

    ``overrides`` is an optional per-run map (see ``agents.pronunciation``) for acronyms the static
    tables have never heard of. It is consulted AFTER both curated tables and can therefore only fill
    gaps, never contradict a decision the operator settled by ear -- ``MLE`` stays "M L E" whatever a
    model would prefer. It exists because spelling alone cannot decide the question: ``MAP`` is
    "em ay pee" in a ranking-metrics sentence and the word "map" anywhere else, and only the
    surrounding line tells you which.
    """

    def repl(m: re.Match) -> str:
        letters, plural = m.group(1), m.group(2)
        custom = _CUSTOM_SOUNDS.get(letters + plural.upper())
        if custom:
            return custom
        if letters in _SPOKEN_AS_WORD:
            return m.group(0)
        if overrides:
            said = overrides.get(letters + plural.upper())
            if said:
                return said
            said = overrides.get(letters)
            if said:
                return _pluralise(said) if plural else said
        sounds = [ch.upper() for ch in letters]
        if plural:
            # Pluralise the LAST letter, the way a person says it: "APIs" is "A P EYES", never
            # "A P I ess". Only the tail needs a phonetic spelling, because only the tail carries a
            # suffix that a bare capital cannot express.
            tail = _LETTER_SOUNDS.get(letters[-1].lower(), letters[-1])
            sounds[-1] = _pluralise(tail)
        return " ".join(sounds)

    return _INITIALISM.sub(repl, text)


def acronyms_in(text: str) -> list[str]:
    """Every distinct capital run in ``text`` that the spelling rule would act on, first seen first.

    Shared with ``agents.pronunciation`` so the set asked about is EXACTLY the set that gets
    transformed -- deriving them with a second regex is how the two would drift apart.
    """
    seen: dict[str, None] = {}
    for m in _INITIALISM.finditer(text or ""):
        seen.setdefault(m.group(1), None)
    return list(seen)


def is_curated(acronym: str) -> bool:
    """True when a hand-verified table already answers for this acronym, so nothing may re-decide it."""
    word = (acronym or "").upper()
    return word in _CUSTOM_SOUNDS or word in _SPOKEN_AS_WORD


def spell_letters(acronym: str) -> str:
    """``SDK`` -> ``S D K``. Spaced capitals, NOT a phonetic respelling.

    A written-out letter name is a guess about how a voice reads an invented word, and the guess kept
    losing. ``ay`` for the letter A is the clearest case: standalone it reads as the interjection
    *aye* (/aI/), so ``AI`` -> "ay eye" was voiced "eye eye" -- and that spelling reached 88 spoken
    occurrences across the channel before anyone caught it. The same trap produced "E M L E" for MLE.
    A bare capital asks the front-end for the LETTER, which is what we wanted all along, and it was
    confirmed by ear on run 0027.

    Exposed so a caller that has merely CLASSIFIED an acronym as letter-by-letter (see
    ``agents.pronunciation``) never has to invent the rendering itself.
    """
    return " ".join(ch.upper() for ch in (acronym or ""))


def _to_words(numstr: str) -> str:
    """A bare number string (``"202,000"`` / ``"1.5"``) to English words, or unchanged on failure."""
    from num2words import num2words

    s = numstr.replace(",", "")
    if s.count(".") == 1:
        whole, frac = s.split(".")
        whole_w = num2words(int(whole)) if whole else "zero"
        frac_w = " ".join(num2words(int(d)) for d in frac) if frac else ""
        return f"{whole_w} point {frac_w}".strip()
    return num2words(int(s))


def spell_designations(text: str) -> str:
    """Say letter-and-number designations with the letter SPACED: ``L5`` -> ``L five``,
    ``SDE2`` -> ``S D E two``, ``P99`` -> ``P ninety-nine``.

    These are everywhere in this niche (Google L5, Meta E3, Amazon SDE2, P99 latency, H100, L1 vs L2
    regularization) and they were being MANGLED BY OUR OWN NORMALIZER, not by the voice: the plain
    number pass rewrote only the digits, gluing the result to the letter and producing "Lfive",
    "Ltwo", "Pninety-nine" -- which a neural voice then read as "lee-five" / "ele-five-el".

    The letter is SPACED rather than respelled, matching ``spell_letters``. This pass used to emit
    "ess dee ee two" on the reasoning that a bare letter leaves the front-end guessing -- but that
    reasoning was written for Chatterbox, and an ear test on IndexTTS-2 settled it the other way. The
    NUMBER still becomes words, because digits are exactly what the voice cannot be trusted with.
    """

    def repl(m: re.Match) -> str:
        letters, digits = m.group(1), m.group(2)
        spoken = " ".join(ch.upper() for ch in letters)
        try:
            return f"{spoken} {_to_words(digits)}"
        except Exception:
            return m.group(0)

    return _DESIGNATION.sub(repl, text)


def speechify_numbers(text: str, overrides: dict[str, str] | None = None) -> str:
    """Expand numerals/currency/percentages/scale-suffixes into words for correct TTS pronunciation.

    ``overrides`` is an optional per-run acronym map; it only ever fills gaps the curated tables in
    this module leave open. See ``spell_initialisms``.

    Best-effort: if ``num2words`` is unavailable, or any token can't be parsed, the original text is
    returned unchanged so synthesis never breaks.
    """
    if not text:
        return text
    try:
        import num2words  # noqa: F401  (presence check; used lazily in _to_words)
    except Exception:  # pragma: no cover - num2words ships in requirements
        return text

    def scaled(m: re.Match) -> str:
        try:
            value = float(m["num"].replace(",", "")) * _SCALE[m["suffix"].lower()]
            value = int(value) if value == int(value) else value
            words = _to_words(str(value))
        except Exception:
            return m.group(0)
        return f"{words} dollars" if m["dollar"] else words

    def currency(m: re.Match) -> str:
        scale = (m["scale"] or "").lower()
        try:
            value = float(m["num"].replace(",", ""))
            # A dead decimal reads aloud: "$1.0 million" would be "one point zero million".
            words = _to_words(str(int(value)) if value == int(value) else m["num"])
        except Exception:
            return m.group(0)
        amount = f"{words} {scale}" if scale else words
        return f"{amount} {'dollar' if value == 1 and not scale else 'dollars'}"

    def percent(m: re.Match) -> str:
        try:
            return f"{_to_words(m['num'])} percent"
        except Exception:
            return m.group(0)

    def times(m: re.Match) -> str:
        try:
            return f"{_to_words(m['num'])} times"
        except Exception:
            return m.group(0)

    def plain(m: re.Match) -> str:
        try:
            return _to_words(m.group(0))
        except Exception:
            return m.group(0)

    def unit(m: re.Match) -> str:
        try:
            word = _UNIT_WORDS[m.group(2).lower()]
            spoken = _to_words(m.group(1))
            return f"{spoken} {word}" if spoken == "one" else f"{spoken} {word}s"
        except Exception:
            return m.group(0)

    # Units before the scale/plain passes, which would otherwise eat the digits and strand the
    # suffix ("20ms" -> "twentyms").
    text = _UNIT.sub(unit, text)
    text = _SCALED.sub(scaled, text)
    text = _CURRENCY.sub(currency, text)
    text = _PERCENT.sub(percent, text)
    text = _TIMES.sub(times, text)
    # Designations sit in a NARROW WINDOW, and both walls are real:
    #   * AFTER _SCALED, because this pass emits capitals now ("M5" -> "M five") and _SCALED reads a
    #     capital M/K/B following a number as a million/thousand/billion suffix -- run it earlier and
    #     "3 MP3" becomes "three million P three", the same trap that pushed spell_initialisms last;
    #   * BEFORE _PLAIN, because a designation is the one pattern with a letter GLUED to its digits,
    #     so the plain pass would rewrite only the number and leave "Lfive" behind.
    text = spell_designations(text)
    text = _PLAIN.sub(plain, text)
    # Pure-letter acronyms LAST, once every digit in the line is already a word. Order matters both
    # ways here:
    #   * the overrides EMIT capitals now ("MLE" -> "M L E"), and _SCALED reads a capital M/K/B after
    #     a number as a scale suffix -- run this first and "3 MLEs" becomes "three million L ees";
    #   * spelling a capital run first also hides it from _UNIT, which is how "16 GB" used to end up
    #     as "sixteen jee bee" instead of "sixteen gigabytes".
    # Nothing above emits capitals (num2words and the letter tables are all lowercase), so this pass
    # only ever sees acronyms the writer actually typed.
    return spell_initialisms(text, overrides)
