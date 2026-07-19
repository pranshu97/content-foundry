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


@dataclass(frozen=True)
class _Platform:
    key: str  # canonical key, also scanned for (with aliases) in the script narration
    label: str  # display name in the resources block
    blurb: str  # "good for ..." — shown to the script AND in the description line
    tags: frozenset[str]  # topics this resource fits
    settings_attr: str  # the Settings attribute holding the operator's referral URL
    aliases: tuple[str, ...] = ()


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
    ),
)

_WORD = re.compile(r"[a-z0-9]+")
# A real Amazon PRODUCT page (…/dp/ASIN or …/gp/product/ASIN). We never emit anything else.
_AMAZON_PRODUCT = re.compile(
    r"amazon\.([a-z.]+)/(?:[^\s?]*?/)?(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE
)


def _enabled(settings) -> bool:
    return bool(getattr(settings, "affiliate_enabled", False))


def _referral_url(settings, p: _Platform) -> str:
    return (getattr(settings, p.settings_attr, "") or "").strip()


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


def amazon_search_query(tags, niche: str) -> str:
    """A book search query from the video's most SPECIFIC topic tag (skipping the bare niche), so the
    Amazon result is a canonical, on-topic book rather than a generic one."""
    niche_l = (niche or "").strip().lower()
    specific = [t.strip() for t in (tags or []) if t and t.strip() and t.strip().lower() != niche_l]
    topic = specific[0] if specific else (niche or "tech career")
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


def affiliate_block(links: list[AffiliateLink], settings) -> str:
    """The description/comment 'Resources' section + a required affiliate disclosure. Empty when there
    are no links."""
    if not links:
        return ""
    header = (getattr(settings, "affiliate_header", "") or "Resources & tools:").strip()
    lines = [header]
    for lk in links:
        lines.append(f"→ {lk.label} ({lk.blurb}): {lk.url}" if lk.blurb else f"→ {lk.label}: {lk.url}")
    disclosure = (getattr(settings, "affiliate_disclosure", "") or "").strip()
    if disclosure:
        lines.extend(["", disclosure])
    return "\n".join(lines)


def affiliate_context(settings, *, niche: str = "", topic: str = "") -> str:
    """A prompt block the script generator MAY use to recommend a resource, or ``""`` when affiliate
    is off / nothing configured. FakeLLM-safe: contains no 'judge' word."""
    if not _enabled(settings):
        return ""
    plats = enabled_platforms(settings)
    has_amazon = bool((getattr(settings, "amazon_assoc_tag", "") or "").strip())
    if not plats and not has_amazon:
        return ""
    lines = [
        "AFFILIATE RESOURCES (optional monetization — use with taste, only where it genuinely helps "
        "the viewer):",
        "- You MAY naturally recommend ONE (at most two) of the resources below WHERE IT TRULY FITS "
        "the advice, and say the link is in the description \u2014 e.g. \"a tool like LeetCode is great "
        "for company-specific problems, I'll leave a link in the description\". Frame it as a "
        "RECOMMENDATION, NEVER a fabricated personal claim: do NOT say \"I used X\" or invent having "
        "used it; say \"X is great for Y\" / \"I'll drop a link to X below\".",
        "- Only mention one that a smart viewer would actually find useful here; NEVER force it, never "
        "list them all, never let it derail or cheapen the video. If none fit, mention none.",
        "  Available resources:",
    ]
    lines.extend(f"    - {p.label}: {p.blurb}" for p in plats)
    if has_amazon:
        lines.append(
            "    - a relevant BOOK: where a book is the natural resource, mention \"a great book on "
            "<this topic>\" and say the link is in the description (the exact book is found and linked "
            "for you)."
        )
    return "\n".join(lines)
