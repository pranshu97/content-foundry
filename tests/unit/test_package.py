"""package.md assembly — the human-facing summary, incl. the brainstormed-idea section."""

from __future__ import annotations

from content_foundry.models import IdeaSelection, Provenance, Script
from content_foundry.pipeline.package import build_package_md


def _script() -> Script:
    return Script(
        run_id="0001",
        template_id="contrarian",
        title_options=["A Great Title"],
        hook="Hook line.",
        provenance=Provenance(produced_by="test"),
    )


def test_package_shows_picked_idea_and_alternatives():
    ideas = IdeaSelection(
        run_id="0001",
        seed="getting into FAANG",
        source="brainstorm",
        generated=["Ace the interview calm", "Resume tricks", "Negotiate the offer"],
        chosen="Resume tricks",
        chosen_index=1,
    )
    md = build_package_md("0001", script=_script(), ideas=ideas)
    assert "## Idea" in md
    assert "**Picked:** Resume tricks" in md
    assert "**Your focus (--idea):** getting into FAANG" in md
    # the chosen idea is not repeated in the alternatives list
    assert "_Also considered:_" in md
    assert "- Ace the interview calm" in md
    assert "- Negotiate the offer" in md
    assert "- Resume tricks" not in md


def test_package_omits_idea_section_when_absent():
    assert "## Idea" not in build_package_md("0001", script=_script(), ideas=None)
    empty = IdeaSelection(run_id="0001", generated=[], chosen="", source="seed")
    assert "## Idea" not in build_package_md("0001", script=_script(), ideas=empty)
