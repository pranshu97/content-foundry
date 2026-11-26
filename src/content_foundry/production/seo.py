"""Deterministic discoverability metadata — titles, tags, chapters, description (future plans 4-5).

Legitimate, platform-compliant SEO only: tighter titles, de-duplicated relevant tags, YouTube
chapter markers, and hashtags. No engagement-baiting, vote manipulation, or policy-violating
tricks. Pure functions of the script + visuals (no LLM, no network). The disclosure sentence is
*not* added here — the Publisher remains the single owner of the non-negotiable disclosure gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..models import DataBrief, ResearchBrief, Script, VisualPackage
from .captions import citation_label, source_only

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
    stripped = re.sub(r"\s*(?:·|\|)?\s*source:\s*.+$", "", text or "", flags=re.IGNORECASE)
    words = " ".join(stripped.split()).split()
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


def _clean_host(url: str) -> str:
    host = urlparse((url or "").strip()).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_http_url(url: str | None) -> bool:
    u = (url or "").strip()
    return u.startswith(("http://", "https://"))


def _reconstruct_fact_urls(
    run_root: Path | None,
    brief: DataBrief | None = None,
    research: ResearchBrief | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Reconstruct ordered ``fact_ref -> url`` and ``domain -> first_url`` maps from run artifacts.

    Matches ``Orchestrator._augment_brief`` ordering (creator_data + research.points + on-topic
    data_brief.key_facts) so existing runs whose ``script.json`` predates ``source_links`` still
    resolve every cited ``fact_ref`` to its exact source URL.
    """
    creator_urls: list[str] = []
    if run_root is not None:
        meta_path = run_root / "run_meta.json"
        if meta_path.is_file():
            try:
                raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                raw_creator = str(raw_meta.get("creator_data") or "")
                if raw_creator.strip():
                    from ..pipeline.creator_data import creator_key_facts

                    creator_urls = [
                        (kf.citation.url or "").strip() for kf in creator_key_facts(raw_creator)
                    ]
            except Exception:
                creator_urls = []

        if research is None and (run_root / "research.json").is_file():
            try:
                research = ResearchBrief.model_validate_json(
                    (run_root / "research.json").read_text(encoding="utf-8")
                )
            except Exception:
                research = None

        if brief is None and (run_root / "data_brief.json").is_file():
            try:
                brief = DataBrief.model_validate_json(
                    (run_root / "data_brief.json").read_text(encoding="utf-8")
                )
            except Exception:
                brief = None

    research_urls: list[str] = []
    if research is not None and research.points:
        research_urls = [(pt.source_url or "").strip() for pt in research.points if pt.point]

    brief_urls: list[str] = []
    if brief is not None and brief.key_facts:
        if research is not None and research.points:
            from ..pipeline.topic_relevance import filter_to_idea

            idea = (research.idea or brief.topic_seed or brief.niche or "").strip()
            kept = filter_to_idea(list(brief.key_facts), idea=idea, text=lambda kf: kf.statement)
            brief_urls = [(kf.citation.url or "").strip() for kf in kept]
        else:
            brief_urls = [(kf.citation.url or "").strip() for kf in brief.key_facts]

    ordered = creator_urls + research_urls + brief_urls
    by_domain: dict[str, str] = {}
    all_candidates = list(ordered)
    if research is not None:
        all_candidates.extend(research.source_urls or [])
    if brief is not None:
        all_candidates.extend((kf.citation.url or "").strip() for kf in brief.key_facts)

    for u in all_candidates:
        if _is_http_url(u):
            host = _clean_host(u)
            if host and host not in by_domain:
                by_domain[host] = u
            base = host.split(".")[0] if host else ""
            if base and base not in by_domain:
                by_domain[base] = u
    return ordered, by_domain


def collect_embedded_sources(
    script: Script,
    visuals: VisualPackage | None = None,
    *,
    run_root: Path | None = None,
    brief: DataBrief | None = None,
    research: ResearchBrief | None = None,
) -> list[tuple[str, str]]:
    """Return ordered, deduplicated ``(label, url)`` pairs for every source embedded in the video.

    Covers both on-screen motion-graphics ``SOURCE // ...`` badges and burned top-strip citations
    (``citations.srt``), resolving URLs from ``scene.source_url``, ``script.source_links``, or the
    run's saved ``research.json`` / ``data_brief.json`` artifacts.
    """
    fact_urls, domain_map = _reconstruct_fact_urls(run_root, brief=brief, research=research)
    for item in getattr(script, "source_links", None) or []:
        if isinstance(item, dict) and _is_http_url(item.get("url")):
            u = str(item["url"]).strip()
            h = _clean_host(u)
            if h and h not in domain_map:
                domain_map[h] = u

    vis_by_scene = (
        {sv.scene_index: sv for sv in visuals.scenes}
        if (visuals is not None and visuals.scenes)
        else {}
    )

    out: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for scene in sorted(script.scenes, key=lambda s: s.index):
        sv = vis_by_scene.get(scene.index)
        embedded_hints: list[str] = []
        if sv is not None:
            for sh in sv.shots or []:
                if getattr(sh, "visual_type", "") == "programmatic_graphic":
                    src_prop = str(
                        (getattr(sh, "template_props", None) or {}).get("source") or ""
                    ).strip()
                    if src_prop:
                        embedded_hints.append(src_prop)
            cit_lbl = citation_label(sv.on_screen_text or "")
            if cit_lbl:
                embedded_hints.append(cit_lbl)
        else:
            if getattr(scene, "visual_source", None):
                embedded_hints.append(str(scene.visual_source).strip())
            raw_ost = source_only(scene.on_screen_text or "")
            if raw_ost:
                embedded_hints.append(raw_ost.split(":", 1)[-1].strip())
            if getattr(scene, "source_url", None):
                embedded_hints.append(str(scene.source_url).strip())

        if not embedded_hints:
            continue

        resolved_url = ""
        sc_url = getattr(scene, "source_url", None)
        if _is_http_url(sc_url):
            resolved_url = str(sc_url).strip()
        elif (
            scene.fact_ref is not None
            and 0 <= scene.fact_ref < len(fact_urls)
            and _is_http_url(fact_urls[scene.fact_ref])
        ):
            resolved_url = fact_urls[scene.fact_ref]
        else:
            # Match any domain or token mentioned in on_screen_text / visual_source / shot props.
            ost_src = source_only(scene.on_screen_text or "").split(":", 1)[-1].strip().lower()
            candidates = [ost_src, *(h.lower() for h in embedded_hints)]
            for cand in candidates:
                if not cand:
                    continue
                if _is_http_url(cand):
                    resolved_url = cand
                    break
                cand_clean = cand.removeprefix("www.")
                if cand_clean in domain_map:
                    resolved_url = domain_map[cand_clean]
                    break

        if not _is_http_url(resolved_url):
            continue
        from .visual_router import is_low_tier_source

        if is_low_tier_source(resolved_url):
            continue
        norm_key = resolved_url.rstrip("/")
        if norm_key in seen_urls:
            continue
        seen_urls.add(norm_key)
        label = _clean_host(resolved_url) or "Source"
        out.append((label, resolved_url))

    # Fallback: if scenes carried no per-scene match yet script.source_links was explicitly populated.
    if not out:
        for item in getattr(script, "source_links", None) or []:
            if not isinstance(item, dict):
                continue
            u = str(item.get("url") or "").strip()
            if not _is_http_url(u):
                continue
            norm_key = u.rstrip("/")
            if norm_key in seen_urls:
                continue
            seen_urls.add(norm_key)
            lbl = str(item.get("label") or _clean_host(u) or "Source").strip()
            out.append((lbl, u))

    return out


def format_sources_block(sources: list[tuple[str, str]], *, header: str = "Sources:") -> str:
    """Format deduplicated ``(label, url)`` pairs as the final ``Sources:`` section of a description."""
    lines: list[str] = []
    for label, url in sources:
        u = (url or "").strip()
        if not _is_http_url(u):
            continue
        lbl = (label or "").strip()
        if lbl and lbl.lower() not in (u.lower(), _clean_host(u).lower()):
            lines.append(f"- {lbl}: {u}")
        elif _clean_host(u):
            lines.append(f"- {_clean_host(u)}: {u}")
        else:
            lines.append(f"- {u}")
    if not lines:
        return ""
    return f"{header}\n" + "\n".join(lines)


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
    disclaimer: str = "",
    sources_block: str = "",
) -> str:
    """Compose a discoverable description (CTA + resources + chapters + channel CTA + hashtags +
    Sources at the very end). Disclosure added downstream. ``affiliate`` (resources + disclosure)
    sits ABOVE the chapters so it isn't buried. ``shorts_hashtag`` (e.g. #Shorts) leads the hashtag
    line so YouTube classifies the upload as a Short.
    """
    blocks: list[str] = []
    body = (description or "").strip()
    blocks.append(body)
    cred = (credential or "").strip()
    if cred and cred.lower() not in body.lower():
        blocks.append(cred)
    disc = (disclaimer or "").strip()
    if disc and disc.lower() not in body.lower():
        blocks.append(disc)
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
    # Sources section sits at the VERY LAST of the description per creator convention.
    if sources_block and sources_block.strip():
        blocks.append(sources_block.strip())
    return "\n\n".join(b for b in blocks if b)


# ------------------------------------------------------------------ compose
def _scene_durations(visuals: VisualPackage) -> dict[int, float]:
    return {sv.scene_index: float(sv.duration_sec) for sv in visuals.scenes}


def optimize_metadata(
    script: Script,
    visuals: VisualPackage,
    settings,
    *,
    affiliate_block: str = "",
    sources_block: str = "",
    run_root: Path | None = None,
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
    speed = max(0.25, float(getattr(settings, "video_speed", 1.0) or 1.0))
    items = [
        (durations.get(scene.index, 0.0) / speed, scene.on_screen_text or scene.narration)
        for scene in sorted(script.scenes, key=lambda s: s.index)
    ]
    # Chapters don't apply to a Short (one continuous <60s clip) — skip them there.
    add_chapters = settings.seo_add_chapters and not settings.is_short
    chapters = build_chapters(items) if add_chapters else []
    resolved_sources = sources_block or format_sources_block(
        collect_embedded_sources(script, visuals, run_root=run_root)
    )
    credential = (
        ""
        if getattr(script, "suppress_creator_credential", False)
        else getattr(settings, "creator_credential_line", "")
    )
    description = optimize_description(
        script.description,
        cta=script.cta,
        affiliate=affiliate_block,
        tags=tags,
        chapters=chapters,
        add_chapters=add_chapters,
        channel_cta=channel_cta_block(settings),
        shorts_hashtag=settings.shorts_hashtag if settings.is_short else "",
        credential=credential,
        disclaimer=getattr(script, "disclaimer", ""),
        sources_block=resolved_sources,
    )
    return OptimizedMetadata(title=title, description=description, tags=tags)
