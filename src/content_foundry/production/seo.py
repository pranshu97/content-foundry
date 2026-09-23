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


# ~15 seconds of speech at the ~155 wpm these videos actually run at.
_OPENING_WORDS = 39


def script_opening(script) -> str:
    """The first ~15 seconds a viewer hears, which is what the title has to deliver on."""
    scenes = sorted(getattr(script, "scenes", []) or [], key=lambda s: getattr(s, "index", 0))
    words = " ".join((getattr(s, "narration", "") or "") for s in scenes[:2]).split()
    return " ".join(words[:_OPENING_WORDS])


def opening_congruence(title: str, opening: str) -> float:
    """Share of the title's distinctive words the opening actually says.

    A viewer clicks the TITLE and then hears the OPENING; when the two share no vocabulary the first
    thing they must do is work out whether they are in the right video. Measured across runs
    0019-0030 this sat at 35% on average, and the worst (11%) never said a single one of its title's
    terms in the first fifteen seconds.
    """
    if not opening:
        return 0.0
    # Local import: pipeline/__init__ pulls in the orchestrator, so a module-level import cycles.
    from ..pipeline.topic_relevance import _words

    wanted = _words(title)
    return len(wanted & _words(opening)) / len(wanted) if wanted else 0.0


def pick_title(title_options: list[str], *, max_chars: int, opening: str = "") -> str:
    """Choose the strongest title: within length, then numeric specificity, then how much of the
    title the script's own opening actually delivers, then original order.

    Congruence sits BELOW the length and digit rules and only replaces the arbitrary index tiebreak,
    so it cannot overturn either existing preference. Measured on runs 0019-0030 every improvable
    case was decided by index alone, so that is where the whole gain lives: 5 of 9 runs already had a
    better-matched title among the options the writer produced and this function discarded (0023 by
    28 points). ``opening=""`` scores every candidate 0 and reproduces the old behaviour exactly.
    """
    candidates = [t.strip() for t in (title_options or []) if t and t.strip()]
    if not candidates:
        return "Career Advice"

    def score(item: tuple[int, str]) -> tuple:
        idx, title = item
        return (
            len(title) <= max_chars,
            _has_digit(title),
            opening_congruence(title, opening),
            -idx,
        )

    return max(enumerate(candidates), key=score)[1]


def _truncate(title: str, max_chars: int) -> str:
    if len(title) <= max_chars:
        return title
    clipped = title[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,.:;-")
    return f"{clipped}…"


def optimize_title(title_options: list[str], *, max_chars: int, opening: str = "") -> str:
    """Pick the strongest title and length-bound it. ``opening`` is the script's first ~15 seconds,
    used only to break ties toward the title that opening actually delivers on. The title is
    deliberately NOT year-stamped: a mechanical ``(2026)`` suffix dates an otherwise evergreen title
    (the recurring complaint that the year showed up on every video). When a topic genuinely is a
    specific-year ranking/salary/trend, the writer weaves the year into a title option itself, which
    reads far better than a bolted-on parenthetical."""
    return _truncate(pick_title(title_options, max_chars=max_chars, opening=opening), max_chars)


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


def channel_cta_block(settings) -> str:
    """A short 'subscribe + explore the channel' block appended to EVERY description (long and Short)
    to turn a viewer into a subscriber who watches more. Empty when disabled."""
    if not getattr(settings, "channel_cta_enabled", False):
        return ""
    text = (getattr(settings, "channel_cta_text", "") or "").strip()
    url = (getattr(settings, "youtube_channel_url", "") or "").strip()
    parts = [p for p in (text, f"▶ {url}" if url else "") if p]
    return "\n".join(parts)


# YouTube's Data API rejects a title or description containing a raw angle bracket ('<' or '>') with a
# 400 "invalid description"/"invalid title" error. The auto-built chapters can inherit one from a
# scene's on_screen_text (e.g. "PROCESS > RESULT"), so swap both for accepted look-alike guillemets.
_YOUTUBE_ANGLE = str.maketrans({"<": "‹", ">": "›"})


def youtube_safe_text(text: str) -> str:
    """Make ``text`` safe for a YouTube title / description / comment: replace the API-forbidden angle
    brackets ``<`` and ``>`` (which trigger a 400 ``invalidDescription``) with accepted look-alikes.
    Everything else is left untouched; empty/falsy input is returned unchanged."""
    return text.translate(_YOUTUBE_ANGLE) if text else text


def optimize_description(
    description: str,
    *,
    cta: str = "",
    affiliate: str = "",
    tags: list[str] | None = None,
    chapters: list[tuple[str, str]] | None = None,
    add_chapters: bool = True,
    channel_cta: str = "",
    shorts_hashtag: str = "",
    credential: str = "",
) -> str:
    """Compose a discoverable description (CTA + resources + chapters + channel CTA + hashtags).
    Disclosure added downstream. ``affiliate`` (resources + disclosure) sits ABOVE the chapters so it
    isn't buried. ``shorts_hashtag`` (e.g. #Shorts) leads the hashtag line so YouTube classifies the
    upload as a Short.

    ``credential`` sits directly BELOW the body, never in front of it. It used to lead, on the
    reasoning that YouTube hides everything past the first couple of lines behind "...more" -- but
    that traded the wrong thing away: the line is byte-identical on every upload, so it adds no signal
    that could ever distinguish one video from another, while eating the opening that search reads and
    excerpts (measured at 69 of ~157 snippet characters, 44%, across runs 0024-0030). The topic leads;
    the credential still lands high enough to be read. Skipped when it already appears in the body, so
    it can never be said twice.
    """
    blocks: list[str] = []
    body = (description or "").strip()
    blocks.append(body)
    cred = (credential or "").strip()
    if cred and cred.lower() not in body.lower():
        blocks.append(cred)
    if cta and cta.strip() and cta.strip().lower() not in body.lower():
        blocks.append(cta.strip())
    if affiliate and affiliate.strip():
        blocks.append(affiliate.strip())  # resources + disclosure, ABOVE the chapters
    if add_chapters and chapters:
        lines = "\n".join(f"{ts} {label}" for ts, label in chapters)
        blocks.append(f"Chapters:\n{lines}")
    if channel_cta and channel_cta.strip():
        blocks.append(channel_cta.strip())
    tag_parts = hashtags(tags or [], limit=5)
    hashtag = (shorts_hashtag or "").strip()
    if hashtag and hashtag not in tag_parts:
        tag_parts = [hashtag, *tag_parts]
    tag_line = " ".join(tag_parts)
    if tag_line:
        blocks.append(tag_line)
    return "\n\n".join(b for b in blocks if b)


# ------------------------------------------------------------------ compose
def _scene_durations(visuals: VisualPackage) -> dict[int, float]:
    return {sv.scene_index: float(sv.duration_sec) for sv in visuals.scenes}


def optimize_metadata(
    script: Script, visuals: VisualPackage, settings, *, affiliate_block: str = ""
) -> OptimizedMetadata:
    """Full deterministic metadata pass for the Publisher."""
    title = optimize_title(
        script.title_options,
        max_chars=settings.seo_title_max_chars,
        opening=script_opening(script),
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
    # Chapters don't apply to a Short (one continuous <60s clip) — skip them there.
    add_chapters = settings.seo_add_chapters and not settings.is_short
    chapters = build_chapters(items) if add_chapters else []
    description = optimize_description(
        script.description,
        cta=script.cta,
        affiliate=affiliate_block,
        tags=tags,
        chapters=chapters,
        add_chapters=add_chapters,
        channel_cta=channel_cta_block(settings),
        shorts_hashtag=settings.shorts_hashtag if settings.is_short else "",
        credential=getattr(settings, "creator_credential_line", ""),
    )
    return OptimizedMetadata(title=title, description=description, tags=tags)
