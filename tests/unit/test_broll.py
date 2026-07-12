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
    _interleave,
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


def test_interleave_round_robins_and_dedups():
    assert _interleave([["a", "b", "c"], ["b", "x", "y"]]) == ["a", "b", "x", "c", "y"]


class _StubClient:
    def __init__(self, urls, *, enabled=True, boom=False):
        self.enabled = enabled
        self._urls = urls
        self._boom = boom

    def search(self, query):
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
