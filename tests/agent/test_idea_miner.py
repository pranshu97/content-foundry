"""Idea Miner: outlier detection vs channel median, dedup/sort, pinned channels, opt-in gating, and
the proof-tag display + orchestrator merge that surface proven ideas in the picker (Ch. 14.5)."""

from __future__ import annotations

from content_foundry.agents import IdeaMiner
from content_foundry.models import MinedIdea
from content_foundry.pipeline.orchestrator import Orchestrator


class FakeDataClient:
    enabled = True

    def __init__(self, *, channels=None, uploads=None, videos=None, stats=None, search_videos=None):
        self._channels = channels or []            # resolve / search_channel_ids result
        self._uploads = uploads or {}              # channel_id -> uploads playlist id
        self._videos = videos or {}                # playlist_id -> [video_id, ...]
        self._stats = stats or {}                  # video_id -> stat dict
        self._search_videos = search_videos or []  # search_video_ids result (candidate video ids)
        self.searched = None
        self.video_searched = None
        self.resolved = None

    def search_channel_ids(self, query, *, limit):
        self.searched = query
        return self._channels[:limit]

    def search_video_ids(self, query, *, limit):
        self.video_searched = query
        return self._search_videos[:limit]

    def resolve_channel_ids(self, handles):
        self.resolved = list(handles)
        return self._channels

    def uploads_playlist_id(self, channel_id):
        return self._uploads.get(channel_id)

    def recent_video_ids(self, playlist_id, *, limit):
        return self._videos.get(playlist_id, [])[:limit]

    def video_stats(self, video_ids):
        return [self._stats[v] for v in video_ids if v in self._stats]


def _stat(vid, title, views, *, channel="Chan", channel_id="UC_c", live="none", duration_sec=300):
    return {
        "id": vid, "title": title, "channel_title": channel, "channel_id": channel_id,
        "published_at": "2024-01-01T00:00:00Z", "views": views, "live": live,
        "duration_sec": duration_sec,
    }


def _mining_settings(monkeypatch, *, channels=""):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("IDEA_MINING_ENABLED", "true")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setenv("IDEA_MINING_CHANNELS", channels)
    reset_settings_cache()
    return get_settings()


# ---------------------------------------------------------------- default: search-first
def test_search_finds_topic_outlier(monkeypatch):
    settings = _mining_settings(monkeypatch)  # no pinned channels -> search-first
    base = {f"g{i}": _stat(f"g{i}", f"upload {i}", 100, channel_id="UC_a") for i in range(5)}
    cand = _stat("c1", "AI roles at FAANG explained", 900, channel_id="UC_a")
    client = FakeDataClient(
        search_videos=["c1"], uploads={"UC_a": "UU_a"},
        videos={"UU_a": list(base)}, stats={**base, "c1": cand},
    )
    ideas = IdeaMiner(settings, client).mine("tech careers", focus="AI roles at FAANG")
    assert client.video_searched == "tech careers AI roles at FAANG"  # topic-locked VIDEO search
    assert [i.title for i in ideas] == ["AI roles at FAANG explained"]
    assert ideas[0].multiple >= 3.0  # 900 / channel median 100


def test_search_keeps_only_channel_outliers(monkeypatch):
    settings = _mining_settings(monkeypatch)
    # A relevant, high-view video that only MATCHED its big channel's average is NOT proven.
    base = {f"g{i}": _stat(f"g{i}", f"u{i}", 1000, channel_id="UC_big") for i in range(5)}
    normal = _stat("c1", "Relevant but average video", 1100, channel_id="UC_big")  # 1.1x
    client = FakeDataClient(
        search_videos=["c1"], uploads={"UC_big": "UU_big"},
        videos={"UU_big": list(base)}, stats={**base, "c1": normal},
    )
    assert IdeaMiner(settings, client).mine("tech careers", focus="AI") == []


def test_search_skips_channel_it_cannot_baseline(monkeypatch):
    settings = _mining_settings(monkeypatch)
    base = {f"g{i}": _stat(f"g{i}", f"u{i}", 100, channel_id="UC_ok") for i in range(5)}
    bad = _stat("cbad", "topic vid on a dead channel", 999, channel_id="UC_bad")
    ok = _stat("cok", "great topic vid", 900, channel_id="UC_ok")

    class Boom(FakeDataClient):
        def uploads_playlist_id(self, channel_id):
            if channel_id == "UC_bad":
                raise RuntimeError("404 playlist")
            return super().uploads_playlist_id(channel_id)

    client = Boom(
        search_videos=["cbad", "cok"], uploads={"UC_ok": "UU_ok"},
        videos={"UU_ok": list(base)}, stats={**base, "cbad": bad, "cok": ok},
    )
    ideas = IdeaMiner(settings, client).mine("tech careers", focus="ai")
    assert [i.title for i in ideas] == ["great topic vid"]  # unbaselineable channel skipped, not fatal


def test_search_excludes_shorts(monkeypatch):
    settings = _mining_settings(monkeypatch)
    base = {f"g{i}": _stat(f"g{i}", f"u{i}", 100, channel_id="UC_a") for i in range(5)}
    short = _stat("s1", "viral short", 9000, channel_id="UC_a", duration_sec=30)  # Short -> excluded
    client = FakeDataClient(
        search_videos=["s1"], uploads={"UC_a": "UU_a"},
        videos={"UU_a": list(base)}, stats={**base, "s1": short},
    )
    assert IdeaMiner(settings, client).mine("tech careers", focus="ai") == []


# ---------------------------------------------------------------- pinned: channel-sampling
def test_pinned_channel_outlier_is_topic_ranked(monkeypatch):
    settings = _mining_settings(monkeypatch, channels="UC_a")
    titles = ["Morning routine", "Desk tour", "Weekend vlog", "Coding music",
              "Q and A", "FAANG interview secrets"]
    stats = {
        f"v{i}": _stat(f"v{i}", titles[i], views)
        for i, views in enumerate([100, 120, 90, 110, 100, 800])
    }
    client = FakeDataClient(
        channels=["UC_a"], uploads={"UC_a": "UU_a"},
        videos={"UU_a": list(stats)}, stats=stats,
    )
    ideas = IdeaMiner(settings, client).mine("faang interview")
    assert ideas[0].title == "FAANG interview secrets"  # on-topic 800-view outlier (~7.6x)
    assert client.resolved == ["UC_a"]  # pinned -> resolved, not searched
    assert client.searched is None


def test_pinned_ranks_on_topic_above_off_topic(monkeypatch):
    settings = _mining_settings(monkeypatch, channels="UC_a")
    rows = [
        ("Coding interview tips", 100), ("System design basics", 100), ("Resume review", 100),
        ("Mock interview walkthrough", 100), ("Offer negotiation", 100),
        ("FAANG interview roadmap", 400),      # on-topic outlier, 4x
        ("My gaming PC build 2026", 800),      # off-topic outlier, 8x
    ]
    stats = {f"v{i}": _stat(f"v{i}", title, views) for i, (title, views) in enumerate(rows)}
    client = FakeDataClient(
        channels=["UC_a"], uploads={"UC_a": "UU_a"},
        videos={"UU_a": list(stats)}, stats=stats,
    )
    ideas = IdeaMiner(settings, client).mine("faang interview")
    titles = [i.title for i in ideas]
    assert titles[0] == "FAANG interview roadmap"   # on-topic wins despite the lower multiple
    assert "My gaming PC build 2026" in titles       # off-topic outlier still offered (soft rank)


def test_pinned_skips_bad_channel_without_sinking_the_run(monkeypatch):
    settings = _mining_settings(monkeypatch, channels="UC_bad, UC_good")
    good = {
        f"g{i}": _stat(f"g{i}", t, v)
        for i, (t, v) in enumerate([("a", 100), ("b", 100), ("c", 100), ("d", 100), ("FAANG win", 900)])
    }

    class Boom(FakeDataClient):
        def uploads_playlist_id(self, channel_id):
            if channel_id == "UC_bad":
                raise RuntimeError("404 playlist")
            return super().uploads_playlist_id(channel_id)

    client = Boom(
        channels=["UC_bad", "UC_good"], uploads={"UC_good": "UU_good"},
        videos={"UU_good": list(good)}, stats=good,
    )
    ideas = IdeaMiner(settings, client).mine("faang")
    assert [i.title for i in ideas] == ["FAANG win"]


def test_pinned_uses_resolve_not_search(monkeypatch):
    settings = _mining_settings(monkeypatch, channels="@Creator, UC_raw")
    client = FakeDataClient(channels=["UC_resolved"])  # no uploads -> [] ideas; assert the wiring
    IdeaMiner(settings, client).mine("x")
    assert client.resolved == ["@Creator", "UC_raw"]
    assert client.searched is None
    assert client.video_searched is None  # pinned mode never runs a search


def test_pinned_sorts_by_multiple_and_dedups(monkeypatch):
    settings = _mining_settings(monkeypatch, channels="UC_a, UC_b")
    a = {
        f"a{i}": _stat(f"a{i}", t, v, channel="A")
        for i, (t, v) in enumerate([("n", 100), ("n", 100), ("n", 100), ("n", 100), ("Shared", 1000)])
    }
    b = {
        f"b{i}": _stat(f"b{i}", t, v, channel="B")
        for i, (t, v) in enumerate(
            [("m", 100), ("m", 100), ("m", 100), ("m", 100), ("Shared", 400), ("Unique", 600)]
        )
    }
    client = FakeDataClient(
        channels=["UC_a", "UC_b"],
        uploads={"UC_a": "UU_a", "UC_b": "UU_b"},
        videos={"UU_a": list(a), "UU_b": list(b)},
        stats={**a, **b},
    )
    ideas = IdeaMiner(settings, client).mine("x")
    assert [i.title for i in ideas] == ["Shared", "Unique"]  # sorted desc, dup "Shared" collapsed
    assert ideas[0].views == 1000  # the STRONGER "Shared" (10x from channel A) was kept
    assert ideas[0].multiple >= ideas[1].multiple


def test_pinned_skips_tiny_sample(monkeypatch):
    settings = _mining_settings(monkeypatch, channels="UC_a")
    stats = {f"v{i}": _stat(f"v{i}", f"T{i}", v) for i, v in enumerate([100, 5000, 100])}
    client = FakeDataClient(
        channels=["UC_a"], uploads={"UC_a": "UU_a"},
        videos={"UU_a": list(stats)}, stats=stats,
    )
    assert IdeaMiner(settings, client).mine("x") == []  # <5 sampled videos -> no fair baseline


def test_miner_disabled_returns_empty(settings):
    # Default settings have idea_mining_enabled=False, so nothing is mined even with a live client.
    client = FakeDataClient(search_videos=["c1"])
    assert IdeaMiner(settings, client).mine("x") == []


def test_mined_idea_display_tag():
    idea = MinedIdea(title="Are devices listening", channel_title="Veritasium",
                     views=5_000_000, multiple=8.0)
    assert idea.display() == "Are devices listening  [Veritasium — 5M views, 8x avg]"


def test_mined_idea_display_defaults_and_no_multiple():
    idea = MinedIdea(title="Some idea", views=43_000, multiple=1.2)
    assert idea.display() == "Some idea  [YouTube — 43K views]"  # blank channel + weak multiple


def test_merge_idea_options_maps_display_back_to_clean_title():
    proven = [MinedIdea(title="Proven one", channel_title="X", views=1_000_000, multiple=5.0)]
    offered, mapping = Orchestrator._merge_idea_options(proven, ["Brainstormed A", "Brainstormed B"])
    # Proven (tagged) first, then brainstormed (plain); the run commits to the CLEAN title.
    assert offered[0] == "Proven one  [X — 1M views, 5x avg]"
    assert offered[1:] == ["Brainstormed A", "Brainstormed B"]
    assert mapping[offered[0]] == "Proven one"
    assert mapping["Brainstormed A"] == "Brainstormed A"
