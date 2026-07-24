"""Idea-relevance filtering: keep only the facts that genuinely belong to the chosen idea."""

from __future__ import annotations

from content_foundry.pipeline.topic_relevance import filter_to_idea, idea_concepts, overlap


def test_idea_concepts_dedupes_and_expands_shorthand():
    concepts = idea_concepts("The ML System Design Interview")
    assert any({"ml", "machine", "learn"} <= c for c in concepts)  # ml == machine learning, ONE concept
    assert any("system" in c for c in concepts)
    assert all("the" not in c for c in concepts)  # stopword dropped


def test_overlap_counts_each_concept_once_even_with_synonyms():
    concepts = idea_concepts("ML system design")
    # 'machine learning' matches the ml concept ONCE (not twice); 'systems' stems to 'system'.
    assert overlap("machine learning systems architecture", concepts) == 2  # ml + system
    assert overlap("kubernetes networking crash course", concepts) == 0


def test_filter_to_idea_drops_offtopic_keeps_ontopic():
    idea = "ML system design interview"
    facts = [
        "Designing scalable ML systems for recommendation",   # on-topic
        "Machine learning engineer salaries hit $250k",        # ML-adjacent but about SALARY -> 1 concept
        "How to bake sourdough bread at home",                 # totally off-topic
        "System design interview framework for ML models",     # on-topic
    ]
    kept = filter_to_idea(facts, idea=idea, need=2)
    assert "How to bake sourdough bread at home" not in kept
    assert "Machine learning engineer salaries hit $250k" not in kept  # only the 'ml' concept -> < 2
    assert any("scalable ML systems" in k for k in kept)
    assert any("System design interview framework" in k for k in kept)


def test_filter_to_idea_keeps_all_when_idea_too_thin():
    # A single-concept idea can't be judged at need=2 -> nothing is filtered (never over-filter).
    facts = ["anything", "whatever", "unrelated"]
    assert filter_to_idea(facts, idea="python", need=2) == facts


def test_filter_to_idea_works_on_objects_via_text_accessor():
    items = [{"t": "ML system design deep dive"}, {"t": "gardening tips for spring"}]
    kept = filter_to_idea(items, idea="ML system design interview", text=lambda d: d["t"], need=2)
    assert kept == [{"t": "ML system design deep dive"}]
