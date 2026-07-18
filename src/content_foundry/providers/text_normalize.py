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

    text = _SCALED.sub(scaled, text)
    text = _CURRENCY.sub(currency, text)
    text = _PERCENT.sub(percent, text)
    text = _TIMES.sub(times, text)
    text = _PLAIN.sub(plain, text)
    return text
