"""B-roll: Pixabay parsing, multi-source aggregation, picker variety/dedup, factory (Ch. 11.5)."""

from __future__ import annotations

import random
from collections import Counter

import httpx
import respx

from content_foundry.agents.visuals import _broll_source, _BrollPicker
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.providers import build_broll_client
from content_foundry.providers.broll import (
    MultiBrollClient,
    NullBrollClient,
    PexelsBrollClient,
    PixabayBrollClient,
    _interleave,
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


def test_build_broll_client_selects_sources(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    reset_settings_cache()
    assert isinstance(build_broll_client(get_settings()), NullBrollClient)

    monkeypatch.setenv("PEXELS_API_KEY", "k")
    reset_settings_cache()
    assert isinstance(build_broll_client(get_settings()), PexelsBrollClient)  # single source

    monkeypatch.setenv("PIXABAY_API_KEY", "k2")
    reset_settings_cache()
    assert isinstance(build_broll_client(get_settings()), MultiBrollClient)  # both -> aggregate
