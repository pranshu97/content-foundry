"""Deterministic idea-relevance filtering (Ch. 7 extension).

A broad, multi-query web search now pulls in far more signals than before — many only loosely related
to the CHOSEN idea. Feeding those to the Script Generator drags the script off topic (the operator's
"TOTAL deviation from the idea" bug). This keeps ONLY the facts that genuinely belong to the idea,
using concept-level word overlap: light stemming (so plural/verb forms match) and a small synonym map
(so "ML" matches "machine learning") — and it never over-filters an idea too thin to judge.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]+")

# Generic / filler words that don't identify a TOPIC (so they never count as a shared concept).
_STOP = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at", "is", "are", "be", "by",
    "with", "from", "vs", "how", "what", "why", "who", "when", "which", "your", "you", "my", "our",
    "it", "its", "as", "that", "this", "these", "those", "into", "about", "guide", "tips", "tip",
    "best", "top", "new", "way", "ways", "step", "steps", "using", "use", "get", "make", "complete",
    "ultimate", "beginner", "beginners", "explained", "part", "full", "real",
})

# Shorthand -> the spelled-out forms it should also match (so "ML" and "machine learning" are one
# concept). Values are stemmed the same way as the text before comparison.
_SYN = {
    "ml": ("machine", "learning"),
    "ai": ("artificial", "intelligence"),
    "llm": ("language", "model"),
    "llms": ("language", "model"),
}


def _stem(word: str) -> str:
    """Crude suffix stripping so 'systems'->'system', 'designing'->'design', 'scaled'->'scal'... —
    good enough to match plural/verb forms without a real stemmer."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _words(text: str) -> set[str]:
    """Stemmed content words of a text (>=2 chars, no bare numbers)."""
    return {
        _stem(w) for w in _WORD.findall((text or "").lower()) if len(w) >= 2 and not w.isdigit()
    }


def idea_concepts(idea: str) -> list[set[str]]:
    """The idea's DISTINCT salient concepts, each as a set of accepted surface forms — so matching
    "machine learning" counts the "ml" concept ONCE (not thrice), and filler/stopwords are dropped."""
    seen: set[str] = set()
    concepts: list[set[str]] = []
    for raw in _WORD.findall((idea or "").lower()):
        if len(raw) < 2 or raw.isdigit() or raw in _STOP:
            continue
        stem = _stem(raw)
        if stem in seen:
            continue
        seen.add(stem)
        concepts.append({stem, *(_stem(s) for s in _SYN.get(raw, ()))})
    return concepts


def overlap(text: str, concepts: list[set[str]]) -> int:
    """How many of the idea's concepts a text touches."""
    if not concepts:
        return 0
    words = _words(text)
    return sum(1 for forms in concepts if forms & words)


def filter_to_idea(items, *, idea: str, text=lambda x: x, need: int = 2):
    """Keep only ``items`` whose text shares at least ``need`` of the idea's concepts, MOST-RELEVANT
    first. When the idea has fewer than ``need`` salient concepts to judge by, everything is kept (a
    thin idea is never over-filtered). ``text`` extracts the string to score from each item. Returns a
    new list; the input is untouched."""
    concepts = idea_concepts(idea)
    if len(concepts) < need:
        return list(items)
    scored = [(overlap(text(it), concepts), it) for it in items]
    scored.sort(key=lambda si: si[0], reverse=True)  # stable: ties keep their original order
    return [it for score, it in scored if score >= need]
