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
_CURRENCY = re.compile(r"\$(?P<num>\d[\d,]*(?:\.\d+)?)")
_PERCENT = re.compile(r"(?P<num>\d[\d,]*(?:\.\d+)?)\s?%")
_TIMES = re.compile(r"\b(?P<num>\d[\d,]*(?:\.\d+)?)x\b")
_PLAIN = re.compile(r"\d[\d,]*(?:\.\d+)?")

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
    """Say letter-and-number designations as words: ``L5`` -> ``el five``, ``P99`` -> ``pee ninety-nine``.

    These are everywhere in this niche (Google L5, Meta E3, Amazon SDE2, P99 latency, H100, L1 vs L2
    regularization) and they were being MANGLED BY OUR OWN NORMALIZER, not by the voice: the plain
    number pass rewrote only the digits, gluing the result to the letter and producing "Lfive",
    "Ltwo", "Pninety-nine" — which a neural voice then read as "lee-five" / "ele-five-el".

    Spelling the letter out is what makes this permanent: every part of the output is now an ordinary
    English word, so no front-end has to guess at a letter or an alphanumeric token.
    """

    def repl(m: re.Match) -> str:
        letters, digits = m.group(1), m.group(2)
        spoken = " ".join(_LETTER_SOUNDS.get(ch.lower(), ch) for ch in letters)
        try:
            return f"{spoken} {_to_words(digits)}"
        except Exception:
            return m.group(0)

    return _DESIGNATION.sub(repl, text)


def speechify_numbers(text: str) -> str:
    """Expand numerals/currency/percentages/scale-suffixes into words for correct TTS pronunciation.

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
        try:
            return f"{_to_words(m['num'])} dollars"
        except Exception:
            return m.group(0)

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

    # Designations FIRST: they are the only pattern where a letter is glued to a number, and the
    # plain-number pass below would otherwise consume their digits and leave "Lfive" behind.
    text = spell_designations(text)
    text = _SCALED.sub(scaled, text)
    text = _CURRENCY.sub(currency, text)
    text = _PERCENT.sub(percent, text)
    text = _TIMES.sub(times, text)
    text = _PLAIN.sub(plain, text)
    return text
