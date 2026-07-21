"""Affiliate links (optional monetization) — reliable by design.

The platform CATALOG (what each service is good for + the topics it fits) is built in here; the
operator supplies ONLY their referral URL / tracking tag per platform in the config, so there is NO
per-video product curation. Links are chosen DETERMINISTICALLY by topic — a platform is included when
its tags overlap the video's topic OR the script itself named it — never invented by an LLM. Amazon
products come from a REAL product URL found by the search provider (the associate tag is then
appended), so the URL is genuine, not hallucinated (that network step lives in the Publisher; this
module is pure). A required FTC-style disclosure line is always appended.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AffiliateLink:
    label: str
    url: str
    blurb: str = ""
    mention: str = ""  # the human name the writer says / we scan for (platform name or book title)


@dataclass(frozen=True)
class _Platform:
    key: str  # canonical key, also scanned for (with aliases) in the script narration
    label: str  # display name in the resources block
    blurb: str  # "good for ..." — shown to the script AND in the description line
    tags: frozenset[str]  # topics this resource fits
    settings_attr: str  # the Settings attribute holding the operator's full referral URL
    aliases: tuple[str, ...] = ()
    id_attr: str = ""  # optional Settings attr holding just an affiliate ID (not a full URL)
    id_template: str = ""  # URL built from that ID, e.g. "https://x.com/?aff={id}"
    topic_template: str = ""  # topic-aware URL from {id} + {topic}, e.g. a search/create link


# Built-in catalog. The operator only pastes a referral URL per platform (blank => that platform is
# skipped). Extend this list to add a platform; no per-video work.
_PLATFORMS: tuple[_Platform, ...] = (
    _Platform(
        "algoexpert", "AlgoExpert", "curated coding-interview prep with video explanations",
        frozenset({"interview", "interviews", "coding", "leetcode", "faang", "algorithms", "algorithm",
                   "dsa", "swe", "software", "engineer", "engineering"}),
        "affiliate_algoexpert_url", ("algo expert",),
    ),
    _Platform(
        "exponent", "Exponent", "system-design, PM and behavioral interview prep",
        frozenset({"interview", "interviews", "system", "design", "behavioral", "pm", "product",
                   "manager", "management", "faang"}),
        "affiliate_exponent_url",
    ),
    _Platform(
        "leetcode", "LeetCode", "company-specific coding practice problems",
        frozenset({"interview", "interviews", "coding", "leetcode", "algorithms", "algorithm", "dsa",
                   "practice", "faang", "problems"}),
        "affiliate_leetcode_url", ("leet code",),
    ),
    _Platform(
        "coursera", "Coursera", "university-backed courses in ML, data and CS",
        frozenset({"course", "courses", "learn", "learning", "ml", "machine", "data", "science",
                   "ai", "python", "certificate", "specialization"}),
        "affiliate_coursera_url",
    ),
    _Platform(
        "udemy", "Udemy", "affordable, practical courses on almost any tech skill",
        frozenset({"course", "courses", "learn", "learning", "python", "web", "data", "skill",
                   "skills", "bootcamp", "ai", "ml", "project"}),
        "affiliate_udemy_url",
    ),
    _Platform(
        "educative", "Educative", "text-based interactive courses (system design, coding)",
        frozenset({"course", "courses", "learn", "learning", "system", "design", "coding",
                   "interview", "interviews", "grokking"}),
        "affiliate_educative_url",
        id_attr="affiliate_educative_id",
        id_template="https://www.educative.io/explore?aff={id}",
        # A topic SEARCH lands the viewer on courses for THIS video's subject (validated URL scheme).
        topic_template="https://www.educative.io/search?query={topic}&aff={id}",
    ),
    _Platform(
        "fenzo", "Fenzo AI",
        "AI that spins up a free, interactive course on THIS exact topic in about a minute",
        frozenset({"course", "courses", "learn", "learning", "ai", "ml", "machine", "data",
                   "python", "skill", "skills", "study", "tutorial", "beginner", "beginners"}),
        "affiliate_fenzo_url", ("fenzo",),
        id_attr="affiliate_fenzo_id",
        # Fenzo's referral param is ?ref= (not ?aff=). It's a course GENERATOR — the viewer types the
        # topic on the homepage — so there's no reliable per-topic URL; the blurb frames the topic.
        id_template="https://fenzo.ai/?ref={id}",
    ),
)

_WORD = re.compile(r"[a-z0-9]+")
# A real Amazon PRODUCT page (…/dp/ASIN or …/gp/product/ASIN). We never emit anything else.
_AMAZON_PRODUCT = re.compile(
    r"amazon\.([a-z.]+)/(?:[^\s?]*?/)?(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE
)


def _enabled(settings) -> bool:
    return bool(getattr(settings, "affiliate_enabled", False))


def _referral_url(settings, p: _Platform, *, topic: str = "") -> str:
    """The operator's link for a platform: a full referral URL if configured; else, from a bare
    affiliate ID, a TOPIC-aware URL (``topic_template``, when a topic is known) or the generic
    ``id_template``; else empty (the platform is skipped)."""
    url = (getattr(settings, p.settings_attr, "") or "").strip()
    if url:
        return url
    if p.id_attr:
        aff_id = (getattr(settings, p.id_attr, "") or "").strip()
        if aff_id:
            topic = (topic or "").strip()
            if topic and p.topic_template:
                from urllib.parse import quote_plus

                return p.topic_template.format(id=aff_id, topic=quote_plus(topic))
            if p.id_template:
                return p.id_template.format(id=aff_id)
    return ""


def enabled_platforms(settings) -> list[_Platform]:
    """Platforms the operator has configured a referral URL for."""
    return [p for p in _PLATFORMS if _referral_url(settings, p)]


def _vocab(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def select_referrals(settings, *, tags, script_text: str = "") -> list[AffiliateLink]:
    """Static-referral links relevant to this video: platforms NAMED in the script (strongest signal,
    listed first) then platforms whose tags overlap the topic. Empty when affiliate is off."""
    if not _enabled(settings):
        return []
    vocab = _vocab(" ".join(tags or []) + " " + str(getattr(settings, "target_niche", "")))
    script_lower = (script_text or "").lower()
    named: list[_Platform] = []
    matched: list[_Platform] = []
    for p in enabled_platforms(settings):
        names = (p.key, p.label.lower(), *p.aliases)
        if any(n in script_lower for n in names):
            named.append(p)
        elif p.tags & vocab:
            matched.append(p)
    return [AffiliateLink(p.label, _referral_url(settings, p), p.blurb) for p in (*named, *matched)]


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# Framing/filler words that make a headline a worse BOOK-search query (a canonical book isn't titled
# "how to ace ... in 2026"); stripped from the ENDS of the topic so the core SUBJECT leads the query.
_QUERY_FILLER = frozenset({
    "how", "to", "the", "a", "an", "why", "what", "when", "ways", "way", "tips", "tip", "for", "of",
    "your", "my", "best", "top", "ultimate", "complete", "beginners", "beginner", "step", "by",
    "secrets", "secret", "master", "mastering", "learn", "learning", "crack", "cracking", "ace",
    "acing", "become", "becoming", "get", "getting", "land", "landing", "pass", "passing", "in",
    "on", "into", "avoid", "stop", "start", "guide",
})


def _clean_topic(text: str) -> str:
    """Reduce a verbose idea/headline to a tight subject phrase for a BOOK search — drop the year and
    the leading/trailing framing words — so the result is a canonical book on the SUBJECT, not a
    literal match for the whole headline. (Clean topical tags pass through nearly unchanged.)"""
    words = re.findall(r"[A-Za-z0-9+#]+", _YEAR_RE.sub("", text or ""))
    while words and words[0].lower() in _QUERY_FILLER:
        words.pop(0)
    while words and words[-1].lower() in _QUERY_FILLER:
        words.pop()
    return " ".join(words[:5]).strip()


def amazon_search_query(tags, niche: str) -> str:
    """A book search query from the video's most SPECIFIC topic (skipping the bare niche), CLEANED of
    year/framing words, so the Amazon result is a canonical, on-topic book rather than a literal match
    for a verbose headline."""
    niche_l = (niche or "").strip().lower()
    specific = [t.strip() for t in (tags or []) if t and t.strip() and t.strip().lower() != niche_l]
    topic = (_clean_topic(specific[0]) if specific else "") or (niche or "tech career")
    return f"{topic} book"


def tag_amazon_url(url: str, tag: str) -> str | None:
    """Turn a REAL Amazon product URL into an affiliate link by appending the associate tag. Returns
    ``None`` when the URL is not a recognizable Amazon product page, so a junk/guessed URL is never
    emitted."""
    tag = (tag or "").strip()
    m = _AMAZON_PRODUCT.search(url or "")
    if not m or not tag:
        return None
    domain, asin = m.group(1).rstrip("."), m.group(2)
    return f"https://www.amazon.{domain}/dp/{asin}/?tag={tag}"


def resolve_links(
    settings, *, tags, script_text: str = "", amazon_link: AffiliateLink | None = None
) -> list[AffiliateLink]:
    """The final, capped list of affiliate links for a video (referrals + an optional Amazon product).
    Empty when affiliate is off."""
    if not _enabled(settings):
        return []
    links = select_referrals(settings, tags=tags, script_text=script_text)
    if amazon_link:
        links = [*links, amazon_link]
    cap = int(getattr(settings, "affiliate_max_links", 4) or 4)
    return links[:cap]


# ------------------------------------------------ resolve-first (BEFORE generation)
# A promise like "link in the description" / "link below" in the narration.
_LINK_PROMISE = re.compile(r"\b(link|links)\b[^.!?\n]{0,50}\b(description|below|down below)\b", re.I)


def _book_mention(title: str) -> str:
    """A clean, scannable book title from a messy Amazon search-result title."""
    text = re.sub(r"(?i)^amazon\.com\s*:?\s*", "", (title or "").strip())
    text = re.split(r"\s[:|\u2013\-]\s|:\s|\(", text)[0].strip()  # keep the first clause = the title
    return text[:70]


def _topic_query(tags, niche: str) -> str:
    """A tight, cleaned subject phrase (year/framing stripped) for a topic-aware referral URL — the
    most SPECIFIC tag, else the bare niche."""
    niche_l = (niche or "").strip().lower()
    specific = [t.strip() for t in (tags or []) if t and t.strip() and t.strip().lower() != niche_l]
    return (_clean_topic(specific[0]) if specific else "") or (niche or "").strip()


def candidate_platforms(settings, *, tags, niche: str = "") -> list[AffiliateLink]:
    """Configured platforms whose topic tags overlap this video — offered to the writer as things it
    MAY recommend. Platforms that support it get a TOPIC-aware link (e.g. an Educative search for this
    video's subject). Empty when affiliate is off."""
    if not _enabled(settings):
        return []
    vocab = _vocab(" ".join(tags or []) + " " + (niche or ""))
    topic = _topic_query(tags, niche)
    return [
        AffiliateLink(p.label, _referral_url(settings, p, topic=topic), p.blurb, mention=p.label)
        for p in enabled_platforms(settings)
        if p.tags & vocab
    ]


def resolve_amazon(settings, *, queries, search_provider) -> AffiliateLink | None:
    """Search each of ``queries`` (best-first) for a REAL Amazon book and return the FIRST valid tagged
    product (mention = the book title), or ``None`` if none is found / affiliate is off. Multiple
    queries give redundancy — a miss on one is covered by the next. Best-effort (network)."""
    tag = (getattr(settings, "amazon_assoc_tag", "") or "").strip()
    if not _enabled(settings) or not tag or search_provider is None:
        return None
    for query in queries or []:
        try:
            results = search_provider.search(f"{query} site:amazon.com", max_results=5)
        except Exception:
            continue
        for result in results or []:
            tagged = tag_amazon_url(getattr(result, "url", ""), tag)
            if tagged:
                return AffiliateLink(
                    "Recommended book (Amazon)", tagged, "a book worth reading on this",
                    mention=_book_mention(getattr(result, "title", "")),
                )
    return None


# A REAL Educative COURSE page (…/courses/<slug>); the aff param appends to ANY Educative URL.
_EDUCATIVE_COURSE = re.compile(r"https?://(?:www\.)?educative\.io/courses/[a-z0-9][a-z0-9-]*", re.I)


def _set_query_param(url: str, param: str, value: str) -> str:
    """Return ``url`` with ``param=value`` set, REPLACING any existing occurrence of that param."""
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parts = urlparse(url)
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() != param.lower()
    ]
    kept.append((param, value))
    return urlunparse(parts._replace(query=urlencode(kept)))


def _course_title(title: str) -> str:
    """A clean, scannable course name from a messy Educative search-result title."""
    text = re.split(r"\s[|\u2013\-]\s", (title or "").strip())[0].strip()  # keep the first clause
    text = re.sub(r"(?i)\s*[-|]\s*educative(\.io)?\s*$", "", text).strip()
    return text[:70]


def resolve_educative(settings, *, queries, search_provider) -> AffiliateLink | None:
    """Find a REAL, specific Educative COURSE for the topic (like the Amazon flow) and append our
    affiliate id — REPLACING any existing ``aff`` — so the link is a concrete, relevant course rather
    than a generic landing/search page. ``None`` when off / no id / no provider / nothing found."""
    aff_id = (getattr(settings, "affiliate_educative_id", "") or "").strip()
    if not _enabled(settings) or not aff_id or search_provider is None:
        return None
    for query in queries or []:
        q = (query or "").strip()
        if not q:
            continue
        try:
            results = search_provider.search(f"{q} course site:educative.io/courses", max_results=6)
        except Exception:
            continue
        for result in results or []:
            m = _EDUCATIVE_COURSE.search(getattr(result, "url", "") or "")
            if m:
                return AffiliateLink(
                    "Educative", _set_query_param(m.group(0), "aff", aff_id),
                    "an interactive course on exactly this",
                    mention=_course_title(getattr(result, "title", "")) or "Educative",
                )
    return None


def resolve_candidates(
    settings, *, idea: str = "", niche: str = "", tags=None, search_provider=None, amazon_queries=None
) -> list[AffiliateLink]:
    """The candidate resources offered to the writer BEFORE generation: tag-matched platforms + a REAL
    Amazon book (searched now, so a book the script mentions always exists). ``amazon_queries`` (from
    a cheap LLM call) are searched best-first for redundancy; absent, one deterministic cleaned query
    is used. Empty when affiliate is off; best-effort. Capped at AFFILIATE_MAX_LINKS."""
    if not _enabled(settings):
        return []
    seed = list(tags or []) or ([idea] if idea else [])
    cands = candidate_platforms(settings, tags=seed, niche=niche)
    # Educative: ONLY when the topic actually fits it (it's already a candidate), upgrade the generic
    # link to a REAL, specific course found via search + our aff (replacing any existing aff).
    if search_provider is not None and any(c.label == "Educative" for c in cands):
        edu_q = [q for q in dict.fromkeys([_topic_query(seed, niche), (idea or "").strip()]) if q]
        edu = resolve_educative(settings, queries=edu_q, search_provider=search_provider)
        if edu:
            cands = [c for c in cands if c.label != "Educative"] + [edu]
    queries = list(amazon_queries or []) or [amazon_search_query(seed, niche)]
    amazon = resolve_amazon(settings, queries=queries, search_provider=search_provider)
    if amazon:
        cands.append(amazon)
    cap = int(getattr(settings, "affiliate_max_links", 4) or 4)
    return cands[:cap]


def _mentions(text_lower: str, link: AffiliateLink) -> bool:
    name = (link.mention or link.label or "").lower().strip()
    return bool(name) and name in text_lower  # full title phrase, or a single distinctive word


def select_used(settings, *, candidates, script_text: str = "") -> list[AffiliateLink]:
    """Which resolved candidates the finished script actually references — a deterministic name-scan of
    the narration (URLs are never invented; they come from ``candidates``). Safety net: if the script
    PROMISES a link ('link in the description') but named nothing we detect, include the top candidate
    so a promise is never empty. Capped at AFFILIATE_MAX_LINKS; empty when affiliate off."""
    if not _enabled(settings) or not candidates:
        return []
    low = (script_text or "").lower()
    used = [c for c in candidates if _mentions(low, c)]
    if not used and _LINK_PROMISE.search(script_text or ""):
        used = [candidates[0]]
    seen: set[str] = set()
    out: list[AffiliateLink] = []
    for c in used:
        if c.url not in seen:
            seen.add(c.url)
            out.append(c)
    cap = int(getattr(settings, "affiliate_max_links", 4) or 4)
    return out[:cap]


def affiliate_block(links: list[AffiliateLink], settings) -> str:
    """The description/comment 'Resources' section + a required affiliate disclosure. Empty when there
    are no links."""
    if not links:
        return ""
    header = (getattr(settings, "affiliate_header", "") or "Resources & tools:").strip()
    lines = [header]
    for lk in links:
        lines.append(f"→ {lk.label} ({lk.blurb}): {lk.url}" if lk.blurb else f"→ {lk.label}: {lk.url}")
    perk = (getattr(settings, "affiliate_perk_text", "") or "").strip()
    if perk:
        lines.extend(["", perk])  # a casual "you may get a discount via my link" aside
    disclosure = (getattr(settings, "affiliate_disclosure", "") or "").strip()
    if disclosure:
        lines.extend(["", disclosure])
    return "\n".join(lines)


def affiliate_context(settings, *, candidates=None) -> str:
    """Prompt block listing the RESOLVED resource candidates (real links already found) the writer MAY
    recommend BY NAME. Empty when affiliate is off or nothing resolved. FakeLLM-safe (no 'judge')."""
    if not _enabled(settings):
        return ""
    cands = list(candidates or [])
    if not cands:
        return ""
    example = cands[0].mention or cands[0].label
    lines = [
        "AFFILIATE RESOURCES (optional monetization — recommend with taste, ONLY where it truly helps "
        "the viewer):",
        "- The resources below are REAL and their links are ALREADY prepared for the "
        "description. When one GENUINELY fits this exact topic, recommend it (one or two): "
        f"name it EXACTLY and say the link is in the description (e.g. \"grab {example} — I'll "
        "leave the link in the description\"). Frame it as a recommendation, never a fabricated "
        "personal use (\"I used X\"). When NONE genuinely fits, name none — never shoehorn an "
        "affiliate resource into an unrelated video.",
        "- CRITICAL: only ever name a resource from THIS list, using its exact name; whatever you name "
        "is what gets linked, so NEVER promise a link for anything not listed. If none fit, name none.",
    ]
    if (getattr(settings, "affiliate_perk_text", "") or "").strip():
        lines.append(
            "- OPTIONAL, only if it flows NATURALLY (never forced or salesy): you MAY add one light "
            "aside that going through your link gets the viewer a discount or free trial. Keep it "
            "casual and short; skip it entirely if it wouldn't feel natural."
        )
    lines.append("  Available resources:")
    for c in cands:
        label = c.mention or c.label
        lines.append(f"    - {label}: {c.blurb}" if c.blurb else f"    - {label}")
    return "\n".join(lines)
