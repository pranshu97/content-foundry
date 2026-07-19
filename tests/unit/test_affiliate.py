"""Affiliate links: deterministic topic selection, Amazon URL tagging, block + disclosure, and the
FakeLLM-safe script context."""

from __future__ import annotations

from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.production.affiliate import (
    AffiliateLink,
    affiliate_block,
    affiliate_context,
    amazon_search_query,
    resolve_links,
    select_referrals,
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
    assert affiliate_context(s, niche="tech careers") == ""


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


def test_affiliate_context_lists_platforms_and_is_fakellm_safe(monkeypatch):
    s = _settings(
        monkeypatch, AFFILIATE_ENABLED="true",
        AFFILIATE_LEETCODE_URL="https://lc", AFFILIATE_ALGOEXPERT_URL="https://ae",
    )
    ctx = affiliate_context(s, niche="tech careers", topic="faang interview")
    assert "LeetCode" in ctx and "AlgoExpert" in ctx
    assert "judge" not in ctx.lower()  # must not misroute the shared FakeLLM


def test_affiliate_context_empty_without_configured_urls(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true")  # enabled but nothing configured
    assert affiliate_context(s, niche="tech") == ""


def test_amazon_search_query_skips_the_bare_niche():
    assert amazon_search_query(
        ["tech careers", "system design", "interview"], "tech careers"
    ) == "system design book"
    assert amazon_search_query([], "tech careers") == "tech careers book"  # fallback to niche


def test_affiliate_context_offers_a_book_when_only_amazon(monkeypatch):
    s = _settings(monkeypatch, AFFILIATE_ENABLED="true", AMAZON_ASSOC_TAG="crackedstudio-20")
    ctx = affiliate_context(s, niche="tech careers", topic="system design")
    assert "book" in ctx.lower() and "judge" not in ctx.lower()
