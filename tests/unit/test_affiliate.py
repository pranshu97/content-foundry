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
    resolve_amazon,
    resolve_candidates,
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


def test_select_used_scans_narration_and_promise_safety_net(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="t-20")
    book = AffiliateLink("Recommended book (Amazon)", "https://www.amazon.com/dp/B0/?tag=t-20",
                         "", mention="Cracking the Coding Interview")
    lc = AffiliateLink("LeetCode", "https://lc", "", mention="LeetCode")
    # Named in the narration -> used:
    used = select_used(
        s, candidates=[book, lc], script_text="grab Cracking the Coding Interview below"
    )
    assert [u.mention for u in used] == ["Cracking the Coding Interview"]
    # Nothing named but a link is PROMISED -> the top candidate is attached (never an empty promise):
    assert select_used(s, candidates=[book, lc], script_text="the link is in the description") == [
        book
    ]
    # No mention, no promise -> nothing:
    assert select_used(s, candidates=[book, lc], script_text="a plain sentence") == []


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
