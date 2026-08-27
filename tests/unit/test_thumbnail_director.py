"""Thumbnail Director (Agent 5.6): the LLM writes a rich, per-video thumbnail image prompt."""

from __future__ import annotations

import pytest

from content_foundry.agents.thumbnail_director import (
    ThumbnailDirector,
    _sanitize,
    strip_career_rank,
)
from content_foundry.config import get_settings, reset_settings_cache


def _settings(monkeypatch, *, enabled: bool):
    monkeypatch.setenv("THUMBNAIL_DIRECTOR_ENABLED", "true" if enabled else "false")
    reset_settings_cache()
    return get_settings()


def test_thumbnail_director_writes_prompt_from_the_description(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    text = (
        "A cinematic thumbnail: a glowing laptop with a red REJECTED stamp, moody blue and orange."
    )
    llm = fakes.LLM(script_json=text)
    out = ThumbnailDirector(settings, llm).compose(
        "some concept",
        title="A Title",
        niche="tech careers",
        description="FAANG interviewers grade you on a hidden scoring matrix and flag 'Hero Behavior'.",
    )
    assert out == text
    # One DRAFT plus one refinement attempt. The fake's reply is not the refiner's JSON contract, so
    # the loop ends immediately and keeps the draft untouched — the graceful-degradation path.
    assert llm.call_count == 2
    # calls[0] is the DRAFT. Later calls are the critique-and-rewrite rounds, which carry the
    # refinement prompt instead — these assertions are about how the first draft is requested.
    user_prompt = llm.calls[0]["prompt"]
    # the EXACT Gemini-style instruction + the raw description are the ONLY content input
    assert user_prompt.startswith(
        "Write a prompt to generate a thumbnail for a youtube video whose description is given below"
    )
    assert "hidden scoring matrix" in user_prompt
    system = llm.calls[0]["system"] or ""
    assert "judge" not in system.lower()
    assert "EXAMPLE 1 PROMPT" in system  # the worked examples are what drive the quality


def test_thumbnail_director_falls_back_to_concept_without_a_description(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a bold cinematic thumbnail")
    ThumbnailDirector(settings, llm).compose("a laptop with a red scorecard", title="t")
    # no description => the concept becomes the description-context handed to the model
    assert "a laptop with a red scorecard" in llm.calls[0]["prompt"]


def test_the_videos_own_claim_is_the_only_source_of_figures(monkeypatch, fakes):
    """A thumbnail read "5%" while the script's first line said "10%".

    The description is SEO copy and almost never carries the video's numbers, so a director working
    from it alone has to INVENT any figure it puts on screen -- and a thumbnail that contradicts the
    opening line reads as a bait-and-switch to the viewer who just clicked it. Passing the spoken
    hook gives the model a real number to use, and the prompt forbids inventing one.
    """
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a thumbnail")
    ThumbnailDirector(settings, llm).compose(
        "c",
        title="t",
        description="Why coding agents cannot replace machine learning engineers.",
        claim="Writing model code is only ten percent of the job.",
    )
    user_prompt = llm.calls[0]["prompt"]
    assert "only ten percent of the job" in user_prompt
    assert "THE VIDEO'S OWN OPENING CLAIM" in user_prompt
    assert "NEVER INVENT A FIGURE" in (llm.calls[0]["system"] or "")


def test_the_claim_is_optional_and_never_leaks_a_label(monkeypatch, fakes):
    """Most callers pass no claim, and an empty one must not append a dangling empty header."""
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a thumbnail")
    ThumbnailDirector(settings, llm).compose("c", title="t", description="d", claim="   ")
    assert "OPENING CLAIM" not in llm.calls[0]["prompt"]


def test_the_writers_headline_is_offered_not_imposed(monkeypatch, fakes):
    """Run 0025 shipped a thumbnail whose biggest words were "90% ARCHITECTURE" -- accurate, and it
    never named the question the video answers. Left with nothing, the model invents a headline and
    reaches for the most striking INTERNAL STATISTIC, i.e. the argument rather than the topic.

    So the writer's own `thumbnail_text` (which already existed, and only reached the fallback card)
    is handed over. But it is an OFFER, not an order: the overlay was removed precisely because a
    good thumbnail says what it is about WITHOUT a caption, so forcing the words back in would undo
    that decision. The picture carries the idea; the line is there for when words genuinely help.
    """
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a thumbnail")
    ThumbnailDirector(settings, llm).compose(
        "c",
        title="t",
        description="Why coding agents cannot replace machine learning engineers.",
        headline="WILL AI REPLACE MLEs?",
    )
    user_prompt = llm.calls[0]["prompt"]
    assert "WILL AI REPLACE MLEs?" in user_prompt
    assert "The PICTURE should carry this idea" in user_prompt
    assert "only if -- the frame genuinely needs " in user_prompt
    system = llm.calls[0]["system"] or ""
    assert "OPTIONAL and usually unnecessary" in system


def test_the_headline_is_optional_and_never_leaks_a_label(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a thumbnail")
    ThumbnailDirector(settings, llm).compose("c", title="t", description="d", headline="  ")
    assert "CREATOR'S OWN THUMBNAIL LINE" not in llm.calls[0]["prompt"]


def test_thumbnail_director_directs_a_single_idea_search_thumbnail(monkeypatch, fakes):
    """The frame must read instantly and belong to THIS video - while keeping the worked examples
    that drive the quality bar."""
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a thumbnail")
    ThumbnailDirector(settings, llm).compose("c", title="t", description="d")
    system = llm.calls[0]["system"] or ""
    assert "SPECIFIC, NOT STOCK" in system
    assert "single focal point" in system
    assert "whiteboard architecture" in system  # the real-artifact route is still offered
    # Nothing is overlaid after generation any more, so the PICTURE must say what the video is.
    assert "THE PICTURE HAS TO SAY IT" in system
    assert "do NOT draw it" not in system
    assert "negative space for a title overlay" in system  # named only to forbid it
    # All three exemplars are the quality lever - a direction block must never replace them.
    for marker in ("EXAMPLE 1 DESCRIPTION", "EXAMPLE 2 DESCRIPTION", "EXAMPLE 3 DESCRIPTION"):
        assert marker in system
    assert "INTERNAL GRADING RUBRIC" in system  # the original worked example survives


def test_the_prompt_teaches_inversion_not_just_explanation(monkeypatch, fakes):
    """A thumbnail may stage the video's ARGUMENT, or stage the thing the viewer FEARS.

    The rulebook had quietly outlawed the second move: "authentic, not abstract" pushed every frame
    toward a literal practitioner artifact, so a video asking "will AI replace me?" could never show
    the robot in the chair -- which is the single most clickable image it has. The inversion has to
    be taught by EXAMPLE, because in this repo the exemplar beats the written rule every time.
    """
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a thumbnail")
    ThumbnailDirector(settings, llm).compose("c", title="t", description="d")
    system = llm.calls[0]["system"] or ""
    assert "TELL THE STORY, OR INVERT IT" in system
    assert "viewer is AFRAID of" in system
    # The third exemplar must actually DEMONSTRATE it, and carry no lettering at all.
    third = system.split("EXAMPLE 3 PROMPT", 1)[1]
    assert "robot" in third.lower() and "lanyard" in third.lower()
    assert third.count('"') == 0, "the wordless exemplar must not contain a drawn label"


def test_the_thumbnail_prompt_rations_words_without_rationing_drama(monkeypatch, fakes):
    """Words are rationed; DRAMA is not.

    An earlier attempt to reduce clutter also stripped the cinematic detail and produced a plain
    whiteboard, strictly worse than the draft it replaced. The prompt has to say which of the two is
    being cut, or the model cuts both.
    """
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a thumbnail")
    ThumbnailDirector(settings, llm).compose("c", title="t", description="d")
    system = llm.calls[0]["system"] or ""
    assert "READABLE ON A PHONE" in system
    assert "Rationing WORDS is not rationing DRAMA" in system
    assert "Keep the theatre" in system
    # The example that teaches the size-disparity trick must actually contain almost no text.
    second = system.split("EXAMPLE 2 PROMPT", 1)[1]
    assert second.count('"') <= 6, "the scale-and-icons example itself carries too many labels"


def test_a_person_is_never_described_by_career_level(monkeypatch, fakes):
    """Image models read "senior" as ELDERLY, not as a rank, so "a senior engineer" renders someone in
    their sixties -- wrong for essentially every video on this channel.

    The rule has to be narrow: the same words are correct as TEXT inside a rubric or chart, and the
    first exemplar legitimately letters "Senior Signal" as a column heading.
    """
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a thumbnail")
    ThumbnailDirector(settings, llm).compose("c", title="t", description="d")
    system = llm.calls[0]["system"] or ""
    assert "NEVER DESCRIBE A PERSON BY CAREER LEVEL" in system
    for word in ("senior", "junior", "veteran", "principal", "experienced"):
        assert f'"{word}"' in system, f"the rule does not name {word}"
    # The carve-out survives: drawn TEXT may still say it.
    assert "Senior Signal" in system
    # And neither exemplar describes a HUMAN by rank.
    for block in system.split("EXAMPLE")[1:]:
        people = block.lower().split("engineer")
        for before in people[:-1]:
            assert not before.rstrip().endswith("senior"), "an exemplar ages its own engineer"


# ---------------------------------------------------- the deterministic backstop for the same trap
@pytest.mark.parametrize(
    ("draft", "expected"),
    [
        # the exact line run 0025 kept regenerating
        (
            "A focused senior AI architect, visible from a side profile",
            "A focused AI architect, visible from a side profile",
        ),
        ("a senior engineer points at the glass", "a engineer points at the glass"),
        ("a veteran data scientist reviews the run", "a data scientist reviews the run"),
        ("two junior developers at a bench", "two developers at a bench"),
        ("a principal machine learning engineer", "a machine learning engineer"),
        ("an experienced backend engineer typing", "an backend engineer typing"),
        # nothing to do
        ("a cinematic shot of an empty server room", "a cinematic shot of an empty server room"),
        ("", ""),
    ],
)
def test_strip_career_rank_removes_the_age_trigger(draft, expected):
    assert strip_career_rank(draft) == expected


def test_strip_career_rank_never_touches_drawn_labels():
    """A thumbnail legitimately LETTERS these words: rewriting a quoted label would corrupt the very
    thing the frame is trying to say."""
    text = (
        'A glass board headed "Senior Signal" beside "L4 Signal", where a senior engineer points at '
        'a box reading "10% AI CODE".'
    )
    out = strip_career_rank(text)
    assert '"Senior Signal"' in out  # the drawn label survives untouched
    assert '"10% AI CODE"' in out
    assert "a engineer points" in out  # the PERSON lost the rank word


def test_the_saved_prompt_path_is_scrubbed_too(monkeypatch, fakes, tmp_path):
    """A prompt SAVED before the rule existed bypasses the director completely, so the rule never
    runs -- which is exactly how run 0025 kept regenerating the same grey-haired architect.
    """
    from content_foundry.agents.thumbnail_director import _sanitize as sanitize

    assert sanitize("A focused senior AI architect at a glass board") == (
        "A focused AI architect at a glass board"
    )


def test_thumbnail_director_disabled_is_noop(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=False)
    llm = fakes.LLM(script_json="unused")
    out = ThumbnailDirector(settings, llm).compose("x", title="y")
    assert out is None
    assert llm.call_count == 0  # disabled -> no LLM call at all


def test_thumbnail_director_adds_no_guardrails_or_avatar_details(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="a bold cinematic thumbnail")
    ThumbnailDirector(settings, llm).compose(
        "developer at a desk", title="t", description="A video about ML system design interviews."
    )
    blob = ((llm.calls[0]["system"] or "") + llm.calls[0]["prompt"]).lower()
    # No operator face-matching / avatar guardrails are injected into the director prompt.
    assert "match the presenter" not in blob


def test_thumbnail_director_empty_concept_and_title_is_noop(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    llm = fakes.LLM(script_json="x")
    out = ThumbnailDirector(settings, llm).compose("", title="")
    assert out is None
    assert llm.call_count == 0  # nothing to describe -> no LLM call


def test_thumbnail_director_returns_none_when_output_blank(monkeypatch, fakes):
    settings = _settings(monkeypatch, enabled=True)
    out = ThumbnailDirector(settings, fakes.LLM(script_json="   ")).compose("c", title="t")
    assert out is None  # unusable (blank) model output -> caller falls back to the template


def test_sanitize_strips_fences_labels_and_quotes():
    assert _sanitize('```\nPrompt: "a dramatic scene, no text"\n```') == "a dramatic scene, no text"
    assert _sanitize("  a clean, glossy render  ") == "a clean, glossy render"
    assert _sanitize("") is None
    assert _sanitize("   ") is None


def test_sanitize_caps_length():
    out = _sanitize("word " * 600)
    assert out is not None and len(out) <= 1800
