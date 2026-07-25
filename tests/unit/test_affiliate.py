"""Affiliate links: deterministic topic selection, Amazon URL tagging, block + disclosure, and the
FakeLLM-safe script context."""

from __future__ import annotations

from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.production.affiliate import (
    AffiliateLink,
    affiliate_block,
    affiliate_context,
    amazon_search_query,
    candidate_platforms,
    curated_candidates,
    resolve_amazon,
    resolve_candidates,
    resolve_designgurus,
    resolve_educative,
    resolve_links,
    select_referrals,
    select_used,
    tag_amazon_url,
)


def _settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    return get_settings()


def test_affiliate_disabled_is_a_noop(monkeypatch):
    s = _settings(monkeypatch)  # OFF by default
    assert select_referrals(s, tags=["interview"], script_text="use leetcode") == []
    assert resolve_links(s, tags=["interview"]) == []
    assert resolve_candidates(s, idea="coding interview", niche="tech") == []
    assert affiliate_context(s, candidates=[]) == ""


def test_educative_and_fenzo_affiliate_ids_build_urls(monkeypatch):
    from content_foundry.production.affiliate import (
        _referral_url,
        candidate_platforms,
        enabled_platforms,
    )

    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true",
        AFFILIATE_EDUCATIVE_ID="BXmM", AFFILIATE_FENZO_ID="ZZ99",
    )
    # No topic (bare ID) => generic landing URLs; Fenzo's referral param is ?ref= (not ?aff=).
    urls = {p.key: _referral_url(s, p) for p in enabled_platforms(s)}
    assert urls["educative"] == "https://www.educative.io/explore?aff=BXmM"
    assert urls["fenzo"] == "https://fenzo.ai/?ref=ZZ99"
    # With a topic, Educative becomes a topic SEARCH for the video's subject (url-encoded).
    cands = {c.label: c.url for c in candidate_platforms(s, tags=["system design interview"], niche="tech")}
    assert cands["Educative"] == "https://www.educative.io/search?query=system+design+interview&aff=BXmM"


def test_affiliate_perk_line_is_opt_in(monkeypatch):
    from content_foundry.production.affiliate import AffiliateLink, affiliate_block

    link = AffiliateLink("X", "https://x", "blurb")
    # Off by default (no AFFILIATE_PERK_TEXT) — checked BEFORE the env is set.
    assert "discount" not in affiliate_block([link], _settings(monkeypatch, AFFILIATE_ENABLED="true"))
    # Opt in -> the casual line appears in the block.
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_PERK_TEXT="a discount via my link")
    assert "a discount via my link" in affiliate_block([link], s)


def test_full_affiliate_url_wins_over_id(monkeypatch):
    from content_foundry.production.affiliate import _PLATFORMS, _referral_url

    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true",
        AFFILIATE_EDUCATIVE_URL="https://educative.example/ref", AFFILIATE_EDUCATIVE_ID="BXmM",
    )
    educative = next(p for p in _PLATFORMS if p.key == "educative")
    assert _referral_url(s, educative) == "https://educative.example/ref"  # explicit URL wins over ID


def test_select_referrals_by_tag(monkeypatch):
    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true",
        AFFILIATE_ALGOEXPERT_URL="https://algo.example/ref",
        AFFILIATE_LEETCODE_URL="https://lc.example/ref",
        AFFILIATE_COURSERA_URL="https://coursera.example/ref",
    )
    labels = [lk.label for lk in select_referrals(s, tags=["faang interview prep"], script_text="")]
    assert "AlgoExpert" in labels and "LeetCode" in labels
    assert "Coursera" not in labels  # course tags don't match a pure interview topic


def test_script_named_platform_is_included_first(monkeypatch):
    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true",
        AFFILIATE_COURSERA_URL="https://coursera.example/ref",
        AFFILIATE_LEETCODE_URL="https://lc.example/ref",
    )
    # An off-topic tag, but the script NAMES Coursera -> it's included and listed first.
    links = select_referrals(s, tags=["gardening"], script_text="I'll leave a link to Coursera below")
    assert links and links[0].label == "Coursera"


def test_only_configured_platforms_appear(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_LEETCODE_URL="https://lc.example/ref")
    assert [lk.label for lk in select_referrals(s, tags=["faang interview"], script_text="")] == [
        "LeetCode"
    ]


def test_tag_amazon_url_valid_and_invalid():
    assert (
        tag_amazon_url("https://www.amazon.com/Some-Book/dp/B01ABCDE23/ref=xyz", "mytag-20")
        == "https://www.amazon.com/dp/B01ABCDE23/?tag=mytag-20"
    )
    assert tag_amazon_url("https://www.amazon.com/s?k=book", "mytag-20") is None  # search page
    assert tag_amazon_url("https://example.com/dp/B01ABCDE23", "mytag-20") is None  # not amazon
    assert tag_amazon_url("https://www.amazon.com/dp/B01ABCDE23", "") is None  # no tag


def test_resolve_links_caps_and_appends_amazon(monkeypatch):
    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_MAX_LINKS="2",
        AFFILIATE_ALGOEXPERT_URL="https://a", AFFILIATE_LEETCODE_URL="https://l",
        AFFILIATE_COURSERA_URL="https://c",
    )
    amazon = AffiliateLink("Recommended book (Amazon)", "https://www.amazon.com/dp/B0/?tag=t-20")
    links = resolve_links(s, tags=["interview coding course"], script_text="", amazon_link=amazon)
    assert len(links) == 2  # capped at AFFILIATE_MAX_LINKS


def test_affiliate_block_has_links_and_disclosure(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_LEETCODE_URL="https://lc.example/ref")
    block = affiliate_block(select_referrals(s, tags=["faang interview"], script_text=""), s)
    assert "LeetCode" in block and "https://lc.example/ref" in block
    assert "affiliate" in block.lower()  # disclosure present
    assert affiliate_block([], s) == ""


def test_affiliate_context_lists_candidates_and_is_fakellm_safe(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true")
    cands = [
        AffiliateLink("Recommended book (Amazon)", "https://www.amazon.com/dp/B0/?tag=t-20",
                      "a great read", mention="Cracking the Coding Interview"),
        AffiliateLink("LeetCode", "https://lc", "practice problems", mention="LeetCode"),
    ]
    ctx = affiliate_context(s, candidates=cands)
    assert "Cracking the Coding Interview" in ctx and "LeetCode" in ctx
    assert "judge" not in ctx.lower()  # must not misroute the shared FakeLLM


def test_affiliate_context_empty_when_off_or_no_candidates(monkeypatch):
    s_off = _settings(monkeypatch)  # affiliate OFF
    assert affiliate_context(s_off, candidates=[AffiliateLink("X", "u")]) == ""
    s_on = _settings(monkeypatch, AFFILIATE_ENABLED="true")
    assert affiliate_context(s_on, candidates=[]) == ""


def test_candidate_platforms_by_tag(monkeypatch):
    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true",
        AFFILIATE_LEETCODE_URL="https://lc", AFFILIATE_ALGOEXPERT_URL="https://ae",
        AFFILIATE_COURSERA_URL="https://c",
    )
    labels = [c.label for c in candidate_platforms(s, tags=["faang interview prep"], niche="tech")]
    assert "LeetCode" in labels and "AlgoExpert" in labels
    assert "Coursera" not in labels  # a course platform doesn't match a pure interview topic


def test_amazon_search_query_skips_the_bare_niche():
    assert amazon_search_query(
        ["tech careers", "system design", "interview"], "tech careers"
    ) == "system design book"
    assert amazon_search_query([], "tech careers") == "tech careers book"  # fallback to niche


def test_amazon_search_query_cleans_a_verbose_idea():
    # A verbose headline collapses to a tight subject (year + leading/trailing framing words dropped),
    # so the pre-generation search seeds a canonical book instead of the whole headline:
    assert amazon_search_query(
        ["How to Crack the FAANG Coding Interview in 2026"], "tech careers"
    ) == "FAANG Coding Interview book"
    assert amazon_search_query(["The Ultimate Guide to System Design"], "tech") == "System Design book"


class _FakeSearch:
    def __init__(self, results):
        self._results = results

    def search(self, query, max_results=5):
        return self._results


def _result(title, url):
    from collections import namedtuple

    return namedtuple("R", "title url snippet")(title, url, "")


def test_resolve_amazon_tags_a_real_product_and_names_the_book(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="crackedstudio-20")
    provider = _FakeSearch([
        _result("Amazon.com: an ad", "https://www.amazon.com/s?k=book"),  # not a product page
        _result("Amazon.com: Cracking the Coding Interview: 189 Questions : Books",
                "https://www.amazon.com/Cracking-Coding-Interview/dp/0984782850/ref=x"),
    ])
    link = resolve_amazon(s, queries=["coding interview book"], search_provider=provider)
    assert link is not None
    assert link.url == "https://www.amazon.com/dp/0984782850/?tag=crackedstudio-20"
    assert link.mention == "Cracking the Coding Interview"  # clean title for the prompt + scan


def test_resolve_educative_finds_a_real_course_and_sets_our_aff(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_EDUCATIVE_ID="BXmM")
    provider = _FakeSearch([
        _result("Educative blog post", "https://www.educative.io/blog/system-design"),  # not a course
        _result("Grokking the System Design Interview - Educative",
                "https://www.educative.io/courses/grokking-the-system-design-interview?aff=someoneelse"),
    ])
    link = resolve_educative(s, queries=["system design interview"], search_provider=provider)
    assert link is not None
    # Real course URL, OUR aff appended, the pre-existing aff REPLACED (not duplicated):
    assert link.url == "https://www.educative.io/courses/grokking-the-system-design-interview?aff=BXmM"
    assert link.mention == "Grokking the System Design Interview"  # clean course name for the scan


def test_resolve_educative_none_without_id_or_provider(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_EDUCATIVE_ID="BXmM")
    assert resolve_educative(s, queries=["x"], search_provider=None) is None
    s_noid = _settings(monkeypatch, AFFILIATE_ENABLED="true")
    assert resolve_educative(s_noid, queries=["x"], search_provider=_FakeSearch([])) is None


def test_resolve_designgurus_finds_a_real_course_and_sets_our_aff(monkeypatch):
    from content_foundry.production.affiliate import _is_specific_resource

    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_DESIGNGURUS_ID="8jh38a")
    provider = _FakeSearch([
        _result("DesignGurus blog", "https://www.designgurus.io/blog/system-design"),  # not a course
        _result("Grokking the Coding Interview - DesignGurus",
                "https://www.designgurus.io/course/grokking-the-coding-interview/?aff=someoneelse"),
    ])
    link = resolve_designgurus(s, queries=["coding interview"], search_provider=provider)
    assert link is not None
    # Real /course/ page (singular), OUR aff appended, any pre-existing aff REPLACED (not duplicated);
    # the slug regex normalises off the trailing slash + old query:
    assert link.url == "https://www.designgurus.io/course/grokking-the-coding-interview?aff=8jh38a"
    assert link.mention == "Grokking the Coding Interview"  # clean course name for the scan
    assert _is_specific_resource(link)  # a /course/ page counts as a concrete resolved resource


def test_resolve_designgurus_none_without_id_or_provider(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_DESIGNGURUS_ID="8jh38a")
    assert resolve_designgurus(s, queries=["x"], search_provider=None) is None
    s_noid = _settings(monkeypatch, AFFILIATE_ENABLED="true")
    assert resolve_designgurus(s_noid, queries=["x"], search_provider=_FakeSearch([])) is None


def test_designgurus_affiliate_id_builds_the_courses_url(monkeypatch):
    from content_foundry.production.affiliate import _referral_url, enabled_platforms

    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_DESIGNGURUS_ID="8jh38a")
    # No documented topic-search URL -> the bare ID builds the generic courses page:
    urls = {p.key: _referral_url(s, p) for p in enabled_platforms(s)}
    assert urls["designgurus"] == "https://www.designgurus.io/courses/?aff=8jh38a"
    # Tag-gated to interview/course topics -> a candidate for a fitting video:
    labels = [c.label for c in candidate_platforms(s, tags=["system design interview"], niche="tech")]
    assert "DesignGurus" in labels


def test_resolve_amazon_none_when_off_or_no_provider(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="t-20")
    assert resolve_amazon(s, queries=["x"], search_provider=None) is None
    s_off = _settings(monkeypatch)
    assert resolve_amazon(s_off, queries=["x"], search_provider=_FakeSearch([])) is None


def test_resolve_amazon_multiple_queries_are_redundant(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="t-20")

    class _PerQuery:  # only the 2nd (canonical-title) query finds a product; the 1st is a miss
        def search(self, query, max_results=5):
            if "cracking" in query.lower():
                return [_result("Cracking the Coding Interview : Books",
                                "https://www.amazon.com/x/dp/0984782850/")]
            return []

    link = resolve_amazon(
        s, queries=["nonexistent gibberish book", "Cracking the Coding Interview"],
        search_provider=_PerQuery(),
    )
    assert link is not None and link.url == "https://www.amazon.com/dp/0984782850/?tag=t-20"
    assert link.mention == "Cracking the Coding Interview"


def test_select_used_scans_narration_with_description_safety_net(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="t-20")
    book = AffiliateLink("Recommended book (Amazon)", "https://www.amazon.com/dp/B0/?tag=t-20",
                         "", mention="Cracking the Coding Interview")
    course = AffiliateLink("Educative", "https://www.educative.io/courses/grokking-x?aff=BXmM",
                           "", mention="Grokking X")
    fenzo = AffiliateLink("Fenzo AI", "https://fenzo.ai/?ref=Z", "", mention="Fenzo AI")
    # A specific resource NAMED in the narration -> exactly that one is linked:
    used = select_used(
        s, candidates=[book, course, fenzo], script_text="grab Cracking the Coding Interview below"
    )
    assert [u.mention for u in used] == ["Cracking the Coding Interview"]
    # Narration named nothing -> ALL the concrete topic-resolved resources (book + course) attach, but
    # NOT the generic platform landing page (Fenzo):
    used = select_used(s, candidates=[book, course, fenzo], script_text="a plain sentence")
    assert [u.mention for u in used] == ["Cracking the Coding Interview", "Grokking X"]
    # No concrete resource -> fall back to the single top candidate:
    assert select_used(s, candidates=[fenzo], script_text="a plain sentence") == [fenzo]
    # Nothing to attach when there are no candidates:
    assert select_used(s, candidates=[], script_text="a plain sentence") == []


def test_select_used_always_fills_remaining_space_with_universal_fenzo(monkeypatch):
    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="t-20", AFFILIATE_MAX_LINKS="5"
    )
    book = AffiliateLink("Recommended book (Amazon)", "https://www.amazon.com/dp/B0/?tag=t-20",
                         "", mention="ML System Design Interview")
    course = AffiliateLink("Educative", "https://www.educative.io/courses/x?aff=BXmM",
                           "", mention="Grokking X")
    fenzo = AffiliateLink("Fenzo AI", "https://fenzo.ai/?ref=7jL6", "generates a course on this",
                          mention="Fenzo AI", universal=True)
    # Nothing named -> concrete book + course attach, AND the universal Fenzo fills the remaining space:
    used = select_used(s, candidates=[book, course, fenzo], script_text="a plain sentence")
    assert [u.mention for u in used] == ["ML System Design Interview", "Grokking X", "Fenzo AI"]
    # ...but NEVER exceed AFFILIATE_MAX_LINKS: with the cap at 2 the universal filler has no room:
    s2 = _settings(
        monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="t-20", AFFILIATE_MAX_LINKS="2"
    )
    used2 = select_used(s2, candidates=[book, course, fenzo], script_text="a plain sentence")
    assert [u.mention for u in used2] == ["ML System Design Interview", "Grokking X"]


def test_affiliate_block_shows_the_real_resource_name(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true")
    book = AffiliateLink(
        "Recommended book (Amazon)", "https://www.amazon.com/dp/B0/?tag=t-20",
        "a book worth reading on this", mention="Machine Learning System Design Interview",
    )
    block = affiliate_block([book], s)
    assert "Machine Learning System Design Interview" in block  # the ACTUAL title, not a generic label
    assert "Recommended book (Amazon)" not in block
    assert "https://www.amazon.com/dp/B0/?tag=t-20" in block


def test_affiliate_block_pins_the_amazon_link_to_the_bottom(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true")
    amazon = AffiliateLink("Recommended book (Amazon)", "https://www.amazon.com/dp/B0/?tag=t-20",
                           "a book", mention="Cracking the Coding Interview")
    course = AffiliateLink("DesignGurus", "https://www.designgurus.io/course/grokking?aff=8jh38a",
                           "a course", mention="Grokking the Coding Interview")
    fenzo = AffiliateLink("Fenzo AI", "https://fenzo.ai/?ref=Z", "generates a course", mention="Fenzo AI")
    # Amazon is FIRST in the input, but the rendered list must ALWAYS put it LAST (even below Fenzo):
    res = [ln for ln in affiliate_block([amazon, course, fenzo], s).splitlines() if ln.startswith("→")]
    assert len(res) == 3
    assert "amazon.com" in res[-1]  # the Amazon book is pinned to the bottom
    assert "amazon.com" not in res[0]  # ...never the first item
    assert "designgurus.io" in res[0]  # a course leads


def test_book_and_course_mentions_strip_trailing_ellipsis():
    from content_foundry.production.affiliate import _book_mention, _course_title

    assert _book_mention("Machine Learning System Design Interview...") == (
        "Machine Learning System Design Interview"
    )
    assert _book_mention("Amazon.com: Clean Code : Books") == "Clean Code"
    assert _course_title("Grokking the ML System Design Interview...") == (
        "Grokking the ML System Design Interview"
    )


def test_resolve_amazon_prefers_the_on_topic_book_over_a_generic_first_hit(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="t-20")

    class _PerQuery:  # the FIRST query surfaces a famous but off-topic classic; a later one the real book
        def search(self, query, max_results=5):
            if "pragmatic" in query.lower():
                return [_result("The Pragmatic Programmer", "https://www.amazon.com/dp/020161622X/")]
            if "machine learning system design" in query.lower():
                return [_result("Machine Learning System Design Interview : Books",
                                "https://www.amazon.com/x/dp/1736049127/")]
            return []

    link = resolve_amazon(
        s,
        queries=["The Pragmatic Programmer", "Machine Learning System Design Interview"],
        search_provider=_PerQuery(),
        topic="The ML System Design Interview: A Step-by-Step Blueprint",
    )
    assert link is not None
    # the ON-TOPIC book wins even though the generic classic was the FIRST query's hit:
    assert link.url == "https://www.amazon.com/dp/1736049127/?tag=t-20"
    assert link.mention == "Machine Learning System Design Interview"


def test_resolve_candidates_combines_platforms_and_amazon(monkeypatch):
    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="t-20",
        AFFILIATE_LEETCODE_URL="https://lc",
    )
    provider = _FakeSearch([
        _result("Amazon.com: System Design Interview : Books",
                "https://www.amazon.com/x/dp/B08CMF2CQF/"),
    ])
    cands = resolve_candidates(
        s, idea="system design interview", niche="tech careers", search_provider=provider
    )
    mentions = [c.mention for c in cands]
    assert "LeetCode" in mentions
    assert any("System Design Interview" in m for m in mentions)


def test_resolve_candidates_issues_a_grokking_course_query(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_DESIGNGURUS_ID="8jh38a")
    seen: list[str] = []

    class _Rec:
        def search(self, query, max_results=5):
            seen.append(query)
            return []

    # "software engineering" makes DesignGurus a candidate but matches NOTHING in the curated catalog,
    # so we fall back to the web search. Both Educative and DesignGurus host the "Grokking the ..."
    # courses, so a "grokking <topic>" query is issued to land the exact course page, and the site:
    # filter is DOMAIN-level (not path-restricted) so the search actually returns results.
    resolve_candidates(s, idea="software engineering", niche="tech careers", search_provider=_Rec())
    assert any(q.lower().startswith("grokking") for q in seen)
    assert any(q.endswith("site:designgurus.io") for q in seen)  # domain-level, not .../course


def test_curated_list_matched_before_any_web_search(monkeypatch):
    # The verified catalog is consulted FIRST: a system-design video gets the REAL Grokking course URL
    # (with our aff id) offline, without a single web request.
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_DESIGNGURUS_ID="8jh38a")
    cands = curated_candidates(s, idea="the system design interview", niche="faang")
    dg = [c for c in cands if c.label == "DesignGurus"]
    assert dg, cands
    assert dg[0].url == "https://www.designgurus.io/course/grokking-the-system-design-interview?aff=8jh38a"
    assert "System Design Interview" in dg[0].mention


def test_curated_amazon_book_is_tagged(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="crackedstudio-20")
    cands = curated_candidates(s, idea="machine learning system design interview")
    book = [c for c in cands if c.label == "Recommended book (Amazon)"]
    assert book, cands
    assert book[0].url == "https://www.amazon.com/dp/1736049127/?tag=crackedstudio-20"
    assert "Machine Learning System Design Interview" in book[0].mention


def test_curated_matching_is_intelligent_not_first_row(monkeypatch):
    # "behavioral" must win over the coding course even though both share the generic word "interview".
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_DESIGNGURUS_ID="8jh38a")
    cands = curated_candidates(s, idea="the behavioral interview", niche="faang")
    dg = [c for c in cands if c.label == "DesignGurus"]
    assert dg and dg[0].url.endswith("grokking-behavioral-interview?aff=8jh38a"), dg


def test_curated_hit_skips_the_web_search(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_DESIGNGURUS_ID="8jh38a")
    seen: list[str] = []

    class _Rec:
        def search(self, query, max_results=5):
            seen.append(query)
            return []

    cands = resolve_candidates(
        s, idea="the system design interview", niche="faang", search_provider=_Rec()
    )
    # curated already supplied the DesignGurus course, so we DON'T web-search designgurus.io
    assert not any("designgurus.io" in q for q in seen)
    dg = [c for c in cands if c.label == "DesignGurus"]
    assert dg and dg[0].url.endswith("grokking-the-system-design-interview?aff=8jh38a")


def test_curated_ignores_unrelated_topics(monkeypatch):
    # Intelligent match: an off-niche video attaches nothing from the catalog (no weak/forced matches).
    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true", AFFILIATE_DESIGNGURUS_ID="8jh38a",
        AFFILIATE_EDUCATIVE_ID="BXmM", AMAZON_ASSOC_TAG="crackedstudio-20",
    )
    assert curated_candidates(s, idea="watercolor painting for beginners", niche="art") == []


def test_curated_is_empty_when_affiliate_disabled(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="false", AFFILIATE_DESIGNGURUS_ID="8jh38a")
    assert curated_candidates(s, idea="system design interview") == []


def test_curated_needs_a_configured_platform(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true")  # enabled, but no ids/tag to build a link
    assert curated_candidates(s, idea="system design interview") == []
