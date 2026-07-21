"""B-roll: Pixabay parsing, multi-source aggregation, picker variety/dedup, factory (Ch. 11.5)."""

from __future__ import annotations

import random
from collections import Counter

import httpx
import respx

from content_foundry.agents.visuals import _broll_source, _BrollPicker, _search_terms
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.providers import build_broll_client
from content_foundry.providers.broll import (
    CoverrBrollClient,
    MultiBrollClient,
    NullBrollClient,
    PexelsBrollClient,
    PixabayBrollClient,
    _clip_ok,
    _interleave,
    _off_topic,
    _pick_page,
)


@respx.mock
def test_pixabay_parses_video_hits_prefers_largest():
    respx.get(url__startswith="https://pixabay.com/api/videos/").mock(
        return_value=httpx.Response(200, json={"hits": [
            {"videos": {"large": {"url": "https://cdn.pixabay.com/a.mp4"}, "small": {"url": "s"}}},
            {"videos": {"tiny": {"url": "https://cdn.pixabay.com/b.mp4"}}},
            {"videos": {}},  # no renditions -> skipped
        ]})
    )
    urls = PixabayBrollClient("key").search("office")
    assert urls == ["https://cdn.pixabay.com/a.mp4", "https://cdn.pixabay.com/b.mp4"]


@respx.mock
def test_coverr_parses_video_hits():
    respx.get(url__startswith="https://api.coverr.co/videos").mock(
        return_value=httpx.Response(200, json={"hits": [
            {"urls": {"mp4": "https://storage.coverr.co/videos/a?token=x"}},
            {"urls": {"mp4": "https://storage.coverr.co/videos/b?token=y"}},
            {"urls": {}},  # no mp4 -> skipped
            {},            # no urls object -> skipped
        ]})
    )
    urls = CoverrBrollClient("key").search("office desk")
    assert urls == [
        "https://storage.coverr.co/videos/a?token=x",
        "https://storage.coverr.co/videos/b?token=y",
    ]


def test_pick_page_is_front_biased_and_in_range():
    counts = Counter(_pick_page(random.Random(i)) for i in range(200))
    assert set(counts) <= {1, 2, 3}  # only valid pages are requested
    assert counts[1] > counts[3]  # front-biased: usually the most-relevant first page
    assert _pick_page(random.Random(0), base=0) in {0, 1, 2}  # Coverr pages are 0-indexed


def test_off_topic_filter_logic():
    assert _off_topic("busy modern office", "moon, night, sky") is True  # unrelated stock junk
    assert _off_topic("busy modern office", "office, business, laptop") is False  # on-topic
    assert _off_topic("full moon rising", "moon, night") is False  # the query DID ask for the moon
    assert _off_topic("close up of person smiling", "woman, lipstick, makeup") is True  # cosmetics
    assert _off_topic("happy team", "valentine, love, hearts, romantic") is True  # greeting-card junk
    # The 0013 bug: a stock ANATOMY diagram padded in for "...whiteboard diagram" — it shares the
    # generic word "diagram" but is off-domain, so drop it; a query that asked for it keeps it.
    assert _off_topic(
        "candidate drawing whiteboard diagram", "intestine, anatomy, digestive, diagram, medical"
    ) is True
    assert _off_topic("stethoscope in a clinic", "stethoscope, clinic") is False  # query asked for it
    assert _off_topic("anything at all", "") is False  # no metadata -> only drop on positive evidence


def test_clip_ok_positive_context_drops_unrelated():
    vocab = {"developer", "code", "laptop", "office", "computer", "software", "engineer"}
    # Dodges the denylist but its tags touch nothing in this video -> positive filter drops it:
    assert _clip_ok("woman at computer", "pottery, ceramics, clay", vocab) is False
    # Names an off-topic subject even though it shares a generic word -> denylist drops it:
    assert _clip_ok("woman at computer", "woman, valentine, hearts", vocab) is False
    # An anatomy clip that shares the generic "diagram" with the video vocabulary is still denied:
    assert _clip_ok(
        "candidate drawing whiteboard diagram", "intestine, anatomy, diagram",
        {"coding", "interview", "whiteboard", "diagram"},
    ) is False
    # On-topic clip (tags touch the video's vocabulary) is kept:
    assert _clip_ok("woman at computer", "office, computer, business", vocab) is True
    # No vocabulary known (context off) -> only the denylist applies, unrelated tags pass:
    assert _clip_ok("woman at computer", "pottery, ceramics", set()) is True
    # No tags at all while a vocabulary IS known -> unverifiable bare-URL clip, dropped (this is the
    # 'no evidence' gap that let off-topic Valentine's/greeting padding sneak past the denylist):
    assert _clip_ok("woman at computer", "", vocab) is False


def test_clip_ok_requires_a_specific_query_word_not_just_generic():
    vocab = {"machine", "learning", "interview", "person", "chart", "data", "model", "engineer"}
    # A honey-scraping clip shares only the GENERIC word "person" with the beat -> dropped now that a
    # missing clip falls back to a GENERATED image (before, the generic vocab overlap let it slip in).
    assert _clip_ok("person pointing chart", "person, honey, beekeeper, jar", vocab) is False
    # A clip that actually names the beat's SPECIFIC subject ("chart") is kept.
    assert _clip_ok("person pointing chart", "businessman, pointing, chart, growth", vocab) is True
    # Generic-only beats (no specific word to match on) still fall back to the vocabulary check.
    assert _clip_ok("person standing", "office, person, desk", vocab | {"office", "desk"}) is True


@respx.mock
def test_pixabay_positive_context_drops_unrelated_clip():
    respx.get(url__startswith="https://pixabay.com/api/videos/").mock(
        return_value=httpx.Response(200, json={"hits": [
            {"videos": {"large": {"url": "https://cdn.pixabay.com/code.mp4"}},
             "tags": "developer, code, laptop"},
            {"videos": {"large": {"url": "https://cdn.pixabay.com/valentine.mp4"}},
             "tags": "valentine, love, hearts"},  # off-topic for a software video
        ]})
    )
    urls = PixabayBrollClient("key").search(
        "developer typing", context="developer code laptop office computer software engineer"
    )
    assert urls == ["https://cdn.pixabay.com/code.mp4"]


@respx.mock
def test_pixabay_drops_off_topic_clips_by_tags():
    respx.get(url__startswith="https://pixabay.com/api/videos/").mock(
        return_value=httpx.Response(200, json={"hits": [
            {"videos": {"large": {"url": "https://cdn.pixabay.com/office.mp4"}},
             "tags": "office, business, computer"},
            {"videos": {"large": {"url": "https://cdn.pixabay.com/moon.mp4"}},
             "tags": "moon, night, sky"},  # off-topic for an office query -> dropped
        ]})
    )
    assert PixabayBrollClient("key").search("busy modern office") == [
        "https://cdn.pixabay.com/office.mp4"
    ]


@respx.mock
def test_pixabay_keeps_off_topic_subject_when_query_asks_for_it():
    respx.get(url__startswith="https://pixabay.com/api/videos/").mock(
        return_value=httpx.Response(200, json={"hits": [
            {"videos": {"large": {"url": "https://cdn.pixabay.com/moon.mp4"}}, "tags": "moon, night"},
        ]})
    )
    # The video really is about the moon, so the moon clip is kept.
    assert PixabayBrollClient("key").search("full moon timelapse") == [
        "https://cdn.pixabay.com/moon.mp4"
    ]


@respx.mock
def test_pexels_drops_off_topic_by_url_slug():
    respx.get(url__startswith="https://api.pexels.com/videos/search").mock(
        return_value=httpx.Response(200, json={"videos": [
            {"url": "https://www.pexels.com/video/developer-typing-code-101/",
             "video_files": [{"link": "https://videos.pexels.com/code.mp4", "width": 1920}]},
            {"url": "https://www.pexels.com/video/woman-applying-red-lipstick-202/",
             "video_files": [{"link": "https://videos.pexels.com/lipstick.mp4", "width": 1920}]},
        ]})
    )
    assert PexelsBrollClient("key").search("developer typing code") == [
        "https://videos.pexels.com/code.mp4"
    ]


def test_interleave_round_robins_and_dedups():
    assert _interleave([["a", "b", "c"], ["b", "x", "y"]]) == ["a", "b", "x", "c", "y"]


class _StubClient:
    def __init__(self, urls, *, enabled=True, boom=False):
        self.enabled = enabled
        self._urls = urls
        self._boom = boom

    def search(self, query, *, context=""):
        if self._boom:
            raise RuntimeError("rate limited")
        return list(self._urls)

    def download(self, url):
        return b"V"


def test_multi_broll_combines_and_survives_a_failing_source():
    multi = MultiBrollClient([_StubClient(["a1", "a2"]), _StubClient(["b1"], boom=True)])
    assert multi.enabled is True
    assert multi.search("q") == ["a1", "a2"]  # the boom source is skipped, the other still used


def test_multi_broll_disabled_when_no_enabled_clients():
    assert MultiBrollClient([_StubClient([], enabled=False)]).enabled is False


def test_broll_source_from_url():
    assert _broll_source("https://videos.pexels.com/x.mp4") == "pexels"
    assert _broll_source("https://cdn.pixabay.com/x.mp4") == "pixabay"
    assert _broll_source("https://storage.coverr.co/videos/x?token=y") == "coverr"
    assert _broll_source("https://other.example/x.mp4") == "stock"


def test_cut_pace_maps_editing_hint():
    from content_foundry.agents.visuals import _cut_pace

    assert _cut_pace("fast") < 1.0  # faster cutting -> more, shorter shots
    assert _cut_pace("hold") > 1.0  # holding -> fewer, longer shots
    assert _cut_pace(None) == 1.0  # no hint -> neutral
    assert _cut_pace("whatever") == 1.0  # unknown hint -> neutral


def test_picker_avoids_consecutive_and_caps_reuse():
    picker = _BrollPicker(random.Random("seed"), max_uses=2)
    seq = [picker.pick(["a", "b"]) for _ in range(4)]
    assert all(seq[i] != seq[i + 1] for i in range(len(seq) - 1))  # never back-to-back
    assert picker.pick(["a", "b"]) is None  # 2 clips x cap 2 -> exhausted
    assert all(c <= 2 for c in Counter(seq).values())


def test_picker_prefers_fresh_clips():
    picker = _BrollPicker(random.Random("seed"))
    pool = [f"c{i}" for i in range(8)]
    picks = [picker.pick(list(pool)) for _ in range(5)]
    assert len(set(picks)) == 5  # all distinct while fresh clips remain


def test_picker_never_reuses_a_clip_by_default():
    # The default cap is 1: every clip is used at most once, so a 3-clip pool yields 3 distinct picks
    # and then None — no shot is ever repeated anywhere in the video.
    picker = _BrollPicker(random.Random("seed"))
    pool = ["a", "b", "c"]
    picks = [picker.pick(list(pool)) for _ in range(3)]
    assert sorted(picks) == ["a", "b", "c"]  # each used exactly once
    assert picker.pick(list(pool)) is None  # pool exhausted -> caller must reach for a different clip


def test_picker_varies_across_runs():
    pool = [f"c{i}" for i in range(12)]
    firsts = {
        _BrollPicker(random.Random(rid)).pick(list(pool))
        for rid in ("0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008", "0009", "0010")
    }
    assert len(firsts) > 1  # different runs don't all open on the same clip


def test_picker_favors_more_relevant_clips():
    # Candidates arrive in relevance order (search rank). Across many runs the top-ranked clip should
    # be chosen more often than the least-ranked, while still leaving room for variety.
    pool = ["a", "b", "c", "d"]  # "a" = most relevant
    firsts = Counter(_BrollPicker(random.Random(str(i))).pick(list(pool)) for i in range(60))
    assert firsts["a"] > firsts["d"]


def test_search_terms_shortens_beat_to_keywords():
    # Long LLM phrases are trimmed to a short, balanced stock query (articles/filler dropped, <=4 words).
    assert _search_terms("two professionals shaking hands across an office desk") == (
        "professionals shaking hands office"
    )
    assert _search_terms("a manager and employee talking at a laptop") == (
        "manager employee talking laptop"
    )
    assert _search_terms("office handshake") == "office handshake"  # already short -> unchanged
    assert _search_terms("on the desk") == "on the desk"  # over-stripping to 1 word -> keep context
    # Filler (how/you/should/your/for/each) is dropped, leaving the concrete subject + action.
    assert _search_terms("how you should tailor your resume for each posting") == (
        "tailor resume posting"
    )
    assert _search_terms("") == ""


def test_build_broll_client_selects_sources(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    monkeypatch.setenv("COVERR_API_KEY", "")
    reset_settings_cache()
    assert isinstance(build_broll_client(get_settings()), NullBrollClient)

    monkeypatch.setenv("PEXELS_API_KEY", "k")
    reset_settings_cache()
    assert isinstance(build_broll_client(get_settings()), PexelsBrollClient)  # single source

    monkeypatch.setenv("PIXABAY_API_KEY", "k2")
    reset_settings_cache()
    assert isinstance(build_broll_client(get_settings()), MultiBrollClient)  # both -> aggregate

    # Coverr is an opt-in third source (off unless a key is set).
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    monkeypatch.setenv("COVERR_API_KEY", "k3")
    reset_settings_cache()
    assert isinstance(build_broll_client(get_settings()), CoverrBrollClient)  # coverr alone
