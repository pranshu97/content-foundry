"""Deterministic discoverability metadata — titles, tags, chapters, description (future plans 4-5).

Legitimate, platform-compliant SEO only: tighter titles, de-duplicated relevant tags, YouTube
chapter markers, and hashtags. No engagement-baiting, vote manipulation, or policy-violating
tricks. Pure functions of the script + visuals (no LLM, no network). The disclosure sentence is
*not* added here — the Publisher remains the single owner of the non-negotiable disclosure gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Script, VisualPackage
from .timebox import timebox_title

_MAX_TAG_LEN = 30
_MAX_TAGS_CHARS = 480  # YouTube's combined-tag budget is ~500 chars


@dataclass(frozen=True)
class OptimizedMetadata:
    title: str
    description: str
    tags: list[str]


# --------------------------------------------------------------------- tags
def _normalize_tag(tag: str) -> str:
    return " ".join((tag or "").lower().split())


def optimize_tags(
    tags: list[str],
    *,
    niche: str,
    channel_keywords: list[str] | None = None,
    max_tags: int,
) -> list[str]:
    """Normalise, de-duplicate, and cap tags; seed evergreen niche/channel tags first."""
    seeds = [niche, *(channel_keywords or [])]
    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in [*seeds, *tags]:
        tag = _normalize_tag(raw)
        if not tag or len(tag) > _MAX_TAG_LEN or tag in seen:
            continue
        if max_tags and len(out) >= max_tags:
            break
        if total + len(tag) > _MAX_TAGS_CHARS:
            continue
        out.append(tag)
        seen.add(tag)
        total += len(tag)
    return out


# -------------------------------------------------------------------- title
def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def pick_title(title_options: list[str], *, max_chars: int) -> str:
    """Choose the strongest title: within length, then numeric specificity, then original order."""
    candidates = [t.strip() for t in (title_options or []) if t and t.strip()]
    if not candidates:
        return "Career Advice"

    def score(item: tuple[int, str]) -> tuple:
        idx, title = item
        return (len(title) <= max_chars, _has_digit(title), -idx)

    return max(enumerate(candidates), key=score)[1]


def _truncate(title: str, max_chars: int) -> str:
    if len(title) <= max_chars:
        return title
    clipped = title[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,.:;-")
    return f"{clipped}…"


def optimize_title(
    title_options: list[str], *, year: int, time_box: bool, time_sensitive: bool, max_chars: int
) -> str:
    """Pick, optionally year-stamp (only when the writer flagged the topic ``time_sensitive``), and
    length-bound the published title."""
    title = pick_title(title_options, max_chars=max_chars)
    if time_box and time_sensitive:
        stamped = timebox_title(title, year)
        if len(stamped) <= max_chars:
            title = stamped
    return _truncate(title, max_chars)


# ----------------------------------------------------------------- chapters
def _format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _chapter_label(text: str) -> str:
    words = " ".join((text or "").split()).split()
    return " ".join(words[:7])[:60].strip()


def build_chapters(
    items: list[tuple[float, str]], *, min_chapters: int = 3, min_seconds: float = 10.0
) -> list[tuple[str, str]]:
    """Build ``(timestamp, label)`` chapters from ``(duration, label)`` scene items.

    Returns ``[]`` unless YouTube's rules are satisfiable: at least ``min_chapters`` chapters, each
    lasting at least ``min_seconds``, with the first starting at ``0:00``.
    """
    cleaned = [(max(0.0, float(d)), _chapter_label(lbl)) for d, lbl in items]
    cleaned = [(d, lbl) for d, lbl in cleaned if lbl]
    if len(cleaned) < min_chapters or any(d < min_seconds for d, _ in cleaned):
        return []
    chapters: list[tuple[str, str]] = []
    start = 0.0
    for duration, label in cleaned:
        chapters.append((_format_timestamp(start), label))
        start += duration
    return chapters


# -------------------------------------------------------------- description
def _hashtag(tag: str) -> str:
    parts = re.findall(r"[a-z0-9]+", tag.lower())
    return ("#" + "".join(p.capitalize() for p in parts)) if parts else ""


def hashtags(tags: list[str], *, limit: int = 3) -> list[str]:
    out: list[str] = []
    for tag in tags[:limit]:
        tag_str = _hashtag(tag)
        if tag_str:
            out.append(tag_str)
    return out


def optimize_description(
    description: str,
    *,
    cta: str = "",
    tags: list[str] | None = None,
    chapters: list[tuple[str, str]] | None = None,
    add_chapters: bool = True,
) -> str:
    """Compose a discoverable description (CTA + chapters + hashtags). Disclosure added downstream."""
    blocks = [(description or "").strip()]
    if cta and cta.strip() and cta.strip().lower() not in blocks[0].lower():
        blocks.append(cta.strip())
    if add_chapters and chapters:
        lines = "\n".join(f"{ts} {label}" for ts, label in chapters)
        blocks.append(f"Chapters:\n{lines}")
    tag_line = " ".join(hashtags(tags or [], limit=5))
    if tag_line:
        blocks.append(tag_line)
    return "\n\n".join(b for b in blocks if b)


# ------------------------------------------------------------------ compose
def _scene_durations(visuals: VisualPackage) -> dict[int, float]:
    return {sv.scene_index: float(sv.duration_sec) for sv in visuals.scenes}


def optimize_metadata(script: Script, visuals: VisualPackage, settings) -> OptimizedMetadata:
    """Full deterministic metadata pass for the Publisher."""
    title = optimize_title(
        script.title_options,
        year=settings.effective_content_year,
        time_box=settings.time_box_enabled,
        time_sensitive=script.time_sensitive,
        max_chars=settings.seo_title_max_chars,
    )
    tags = optimize_tags(
        script.tags,
        niche=settings.target_niche,
        channel_keywords=settings.channel_keywords_list,
        max_tags=settings.seo_max_tags,
    )
    durations = _scene_durations(visuals)
    items = [
        (durations.get(scene.index, 0.0), scene.on_screen_text or scene.narration)
        for scene in sorted(script.scenes, key=lambda s: s.index)
    ]
    chapters = build_chapters(items) if settings.seo_add_chapters else []
    description = optimize_description(
        script.description,
        cta=script.cta,
        tags=tags,
        chapters=chapters,
        add_chapters=settings.seo_add_chapters,
    )
    return OptimizedMetadata(title=title, description=description, tags=tags)
