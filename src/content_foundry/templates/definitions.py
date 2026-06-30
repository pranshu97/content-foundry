"""The six structural templates — the engine of "systematic variation" (Ch. 16.2)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    when_to_use: str
    beats: tuple[str, ...]  # ordered beat sheet injected into the generator prompt
    default_perspective: str


PROBLEM_SOLUTION = Template(
    id="problem_solution",
    name="The Problem/Solution Model",
    when_to_use="A painful, common career problem with a data-backed fix.",
    beats=(
        "Name the painful problem",
        "Why the obvious fix fails",
        "The data",
        "The real solution",
        "First step today",
    ),
    default_perspective="second-person, present-tense",
)

MYTH_VS_REALITY = Template(
    id="myth_vs_reality",
    name="Myth vs. Reality",
    when_to_use="A widely-believed career 'truth' the data contradicts.",
    beats=(
        "State the myth",
        "Why people believe it",
        "The contradicting data",
        "The reality",
        "What to do instead",
    ),
    default_perspective="second-person, myth-busting",
)

THREE_STEP = Template(
    id="three_step",
    name="The 3-Step Strategy",
    when_to_use="An achievable goal that fits a clean 3-move plan.",
    beats=(
        "The outcome + stakes",
        "Step 1",
        "Step 2",
        "Step 3",
        "Recap + CTA",
    ),
    default_perspective="second-person, instructional",
)

CONTRARIAN = Template(
    id="contrarian",
    name="The Contrarian Take",
    when_to_use="Conventional advice that is now wrong given the data.",
    beats=(
        "The popular advice",
        "Here's why that's now backwards",
        "Evidence",
        "The contrarian play",
        "Caveats + action",
    ),
    default_perspective="contrarian, skeptical",
)

CASE_STUDY = Template(
    id="case_study",
    name="The Case Study / Story",
    when_to_use="A concrete example/persona illustrating a trend.",
    beats=(
        "Meet the situation",
        "The turning point",
        "What the data shows broadly",
        "The lesson",
        "Apply it to you",
    ),
    default_perspective="third-person narrative, present-tense",
)

DATA_DEEP_DIVE = Template(
    id="data_deep_dive",
    name="The Data Deep-Dive",
    when_to_use="A striking dataset worth unpacking.",
    beats=(
        "The surprising number",
        "Break it down",
        "What's driving it",
        "What it means for you",
        "The move to make",
    ),
    default_perspective="analytical, second-person",
)

ALL_TEMPLATES: tuple[Template, ...] = (
    PROBLEM_SOLUTION,
    MYTH_VS_REALITY,
    THREE_STEP,
    CONTRARIAN,
    CASE_STUDY,
    DATA_DEEP_DIVE,
)
