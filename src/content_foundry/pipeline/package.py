"""Assemble ``package.md`` — the human-facing deliverable (Ch. 19.3)."""

from __future__ import annotations

from ..models import DataBrief, JudgeReport, PublishResult, Script, VisualPackage
from ..safeguards.disclosure import disclosure_checklist


def build_package_md(
    run_id: str,
    *,
    script: Script,
    judge_report: JudgeReport | None = None,
    publish_result: PublishResult | None = None,
    brief: DataBrief | None = None,
    visuals: VisualPackage | None = None,
) -> str:
    title = (
        publish_result.chosen_title
        if publish_result
        else (script.title_options[0] if script.title_options else "Untitled")
    )
    verdict = (
        f"{judge_report.verdict.value} ({judge_report.weighted_total}/10)"
        if judge_report
        else "n/a"
    )
    if publish_result and publish_result.video_url:
        yt = f"{publish_result.video_url} — status: **{publish_result.privacy_status.upper()}**"
        disclosure_set = publish_result.disclosure_set
    else:
        yt = "not published"
        disclosure_set = False

    title_lines = [f"{i + 1}. {t}" for i, t in enumerate(script.title_options)] or ["(none)"]

    lines: list[str] = [
        f"# {title}",
        "",
        f"**Run:** {run_id}   **Template:** {script.template_id}   **Verdict:** {verdict}",
        f"**YouTube:** {yt}",
        "",
        disclosure_checklist(disclosure_set),
        "## Title options",
        *title_lines,
        "",
        "## Description",
        script.description or "(none)",
        "",
        "## Tags",
        ", ".join(script.tags) or "(none)",
        "",
        "## Thumbnail",
        f"{visuals.thumbnail_path if visuals else 'assets/thumbnail.png'} — overlay: "
        f"\"{visuals.thumbnail_text if visuals else script.thumbnail_concept}\"",
        "",
        "## Grounding (facts used)",
    ]

    if brief and brief.key_facts:
        for fact in brief.key_facts:
            url = fact.citation.url or "n/a"
            lines.append(f"- {fact.statement} — {fact.citation.source}, {url}")
    else:
        lines.append("- (brief not available)")

    lines += ["", "## Script (recordable)", f"**HOOK:** {script.hook}", ""]
    for scene in script.scenes:
        ost = f"  _[on-screen: {scene.on_screen_text}]_" if scene.on_screen_text else ""
        lines.append(f"{scene.index + 1}. {scene.narration}{ost}")
    lines += ["", f"**CTA:** {script.cta}", ""]

    return "\n".join(lines)
