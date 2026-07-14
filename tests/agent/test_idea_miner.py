"""Idea Miner: outlier detection vs channel median, dedup/sort, pinned channels, opt-in gating, and
the proof-tag display + orchestrator merge that surface proven ideas in the picker (Ch. 14.5)."""

from __future__ import annotations

from content_foundry.agents import IdeaMiner
from content_foundry.models import MinedIdea
from content_foundry.pipeline.orchestrator import Orchestrator


class FakeDataClient:
    enabled = True

    def __init__(self, *, channels=None, uploads=None, videos=None, stats=None):
        self._channels = channels or []
        self._uploads = uploads or {}
        self._videos = videos or {}
        self._stats = stats or {}
        self.searched = None
        self.resolved = None

    def search_channel_ids(self, query, *, limit):
        self.searched = query
        return self._channels[:limit]

    def resolve_channel_ids(self, handles):
        self.resolved = list(handles)
        return self._channels

    def uploads_playlist_id(self, channel_id):
        return self._uploads.get(channel_id)

    def recent_video_ids(self, playlist_id, *, limit):
        return self._videos.get(playlist_id, [])[:limit]

    def video_stats(self, video_ids):
        return [self._stats[v] for v in video_ids if v in self._stats]


def _stat(vid, title, views, *, channel="Chan", live="none"):
    return {
        "id": vid, "title": title, "channel_title": channel,
        "published_at": "2024-01-01T00:00:00Z", "views": views, "live": live,
    }


def _mining_settings(monkeypatch, *, channels=""):
    from content_foundry.config import get_settings, reset_settings_cache

    monkeypatch.setenv("IDEA_MINING_ENABLED", "true")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setenv("IDEA_MINING_CHANNELS", channels)
    reset_settings_cache()
    return get_settings()


def test_miner_flags_channel_outlier(monkeypatch):
    settings = _mining_settings(monkeypatch)
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
    assert len(ideas) == 1
    assert ideas[0].title == "FAANG interview secrets"  # the on-topic 800-view outlier (~7.6x)
    assert ideas[0].views == 800
    assert ideas[0].multiple >= 3.0
    assert ideas[0].video_url == "https://youtu.be/v5"
    assert client.searched == "faang interview"  # DYNAMIC niche search, no focus


def test_miner_drops_off_topic_outlier(monkeypatch):
    settings = _mining_settings(monkeypatch)
    # The 800-view video is a genuine outlier for its channel but off-topic for a FAANG-interview
    # run, so the relevance gate drops it -> no proven idea is surfaced.
    rows = [
        ("Coding interview tips", 100), ("System design basics", 120), ("Resume review", 90),
        ("Mock interview walkthrough", 110), ("Offer negotiation", 100), ("My gaming PC build", 800),
    ]
    stats = {f"v{i}": _stat(f"v{i}", title, views) for i, (title, views) in enumerate(rows)}
    client = FakeDataClient(
        channels=["UC_a"], uploads={"UC_a": "UU_a"},
        videos={"UU_a": list(stats)}, stats=stats,
    )
    assert IdeaMiner(settings, client).mine("faang interview") == []


def test_miner_includes_focus_in_channel_search(monkeypatch):
    settings = _mining_settings(monkeypatch)
    client = FakeDataClient(channels=["UC_a"])  # no uploads -> [] ideas; we assert the search query
    IdeaMiner(settings, client).mine("software engineering", focus="resume tips")
    assert client.searched == "software engineering resume tips"


def test_miner_disabled_returns_empty(settings):
    # Default settings have idea_mining_enabled=False, so nothing is mined even with a live client.
    client = FakeDataClient(channels=["UC_a"])
    assert IdeaMiner(settings, client).mine("x") == []


def test_miner_skips_tiny_sample(monkeypatch):
    settings = _mining_settings(monkeypatch)
    stats = {f"v{i}": _stat(f"v{i}", f"T{i}", v) for i, v in enumerate([100, 5000, 100])}
    client = FakeDataClient(
        channels=["UC_a"], uploads={"UC_a": "UU_a"},
        videos={"UU_a": list(stats)}, stats=stats,
    )
    assert IdeaMiner(settings, client).mine("x") == []  # <5 sampled videos -> no fair baseline


def test_miner_uses_pinned_channels(monkeypatch):
    settings = _mining_settings(monkeypatch, channels="@Creator, UC_raw")
    client = FakeDataClient(channels=["UC_resolved"])
    IdeaMiner(settings, client).mine("x")
    assert client.resolved == ["@Creator", "UC_raw"]
    assert client.searched is None  # pinned channels bypass the niche search


def test_miner_sorts_by_multiple_and_dedups(monkeypatch):
    settings = _mining_settings(monkeypatch)
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
