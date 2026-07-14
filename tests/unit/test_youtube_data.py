"""Read-only YouTube Data API client: endpoint parsing, pagination, factory, null client (Ch. 14.5)."""

from __future__ import annotations

import httpx
import respx

from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.providers import build_youtube_data_client
from content_foundry.providers.youtube_data import ApiYouTubeDataClient, NullYouTubeDataClient

_BASE = "https://www.googleapis.com/youtube/v3"


@respx.mock
def test_search_channel_ids_parses_and_dedups():
    respx.get(url__startswith=f"{_BASE}/search").mock(
        return_value=httpx.Response(200, json={"items": [
            {"id": {"channelId": "UC_aaa"}, "snippet": {"channelId": "UC_aaa"}},
            {"id": {"channelId": "UC_bbb"}},
            {"snippet": {"channelId": "UC_aaa"}},  # duplicate -> ignored
            {"id": {}},  # no channel id -> skipped
        ]})
    )
    ids = ApiYouTubeDataClient("key").search_channel_ids("faang interviews", limit=5)
    assert ids == ["UC_aaa", "UC_bbb"]


@respx.mock
def test_search_video_ids_parses_and_dedups():
    respx.get(url__startswith=f"{_BASE}/search").mock(
        return_value=httpx.Response(200, json={"items": [
            {"id": {"videoId": "vid_a"}},
            {"id": {"videoId": "vid_b"}},
            {"id": {"videoId": "vid_a"}},  # duplicate -> ignored
            {"id": {"kind": "youtube#channel"}},  # not a video -> skipped
        ]})
    )
    ids = ApiYouTubeDataClient("key").search_video_ids("ai roles at faang", limit=5)
    assert ids == ["vid_a", "vid_b"]


@respx.mock
def test_resolve_channel_ids_passes_raw_ids_and_resolves_handles():
    # A raw UC… id passes straight through (no API call); an @handle resolves via forHandle.
    respx.get(url__startswith=f"{_BASE}/channels").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "UC_fromhandle"}]})
    )
    ids = ApiYouTubeDataClient("key").resolve_channel_ids(
        ["UCalreadyacanonicalid123", "@SomeCreator", "   "]
    )
    assert ids == ["UCalreadyacanonicalid123", "UC_fromhandle"]


@respx.mock
def test_resolve_channel_ids_falls_back_to_username():
    # forHandle finds nothing, so the client retries with the legacy forUsername lookup.
    respx.get(url__startswith=f"{_BASE}/channels").mock(
        side_effect=[
            httpx.Response(200, json={"items": []}),
            httpx.Response(200, json={"items": [{"id": "UC_legacy"}]}),
        ]
    )
    assert ApiYouTubeDataClient("key").resolve_channel_ids(["@Legacy"]) == ["UC_legacy"]


@respx.mock
def test_uploads_playlist_id():
    respx.get(url__startswith=f"{_BASE}/channels").mock(
        return_value=httpx.Response(200, json={"items": [
            {"contentDetails": {"relatedPlaylists": {"uploads": "UU_xyz"}}}
        ]})
    )
    assert ApiYouTubeDataClient("key").uploads_playlist_id("UC_x") == "UU_xyz"


@respx.mock
def test_uploads_playlist_id_missing_channel_is_none():
    respx.get(url__startswith=f"{_BASE}/channels").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    assert ApiYouTubeDataClient("key").uploads_playlist_id("UC_x") is None


@respx.mock
def test_recent_video_ids_paginates_until_limit():
    respx.get(url__startswith=f"{_BASE}/playlistItems").mock(
        side_effect=[
            httpx.Response(200, json={
                "items": [{"contentDetails": {"videoId": f"v{i}"}} for i in range(50)],
                "nextPageToken": "PAGE2",
            }),
            httpx.Response(200, json={
                "items": [{"contentDetails": {"videoId": f"w{i}"}} for i in range(50)],
            }),
        ]
    )
    ids = ApiYouTubeDataClient("key").recent_video_ids("UU_x", limit=60)
    assert len(ids) == 60
    assert ids[0] == "v0"
    assert ids[50] == "w0"


@respx.mock
def test_video_stats_parses_and_coerces_views():
    respx.get(url__startswith=f"{_BASE}/videos").mock(
        return_value=httpx.Response(200, json={"items": [
            {"id": "v1",
             "snippet": {"title": "A", "channelTitle": "Chan", "channelId": "UC_v1",
                         "publishedAt": "2024-01-01T00:00:00Z"},
             "statistics": {"viewCount": "1500"}, "contentDetails": {"duration": "PT5M30S"}},
            {"id": "v2",
             "snippet": {"title": "B", "liveBroadcastContent": "live"},
             "statistics": {}},  # no viewCount -> 0
        ]})
    )
    stats = ApiYouTubeDataClient("key").video_stats(["v1", "v2"])
    assert stats[0] == {
        "id": "v1", "title": "A", "channel_title": "Chan", "channel_id": "UC_v1",
        "published_at": "2024-01-01T00:00:00Z", "views": 1500, "live": "none", "duration_sec": 330,
    }
    assert stats[1]["views"] == 0
    assert stats[1]["live"] == "live"


def test_null_client_is_disabled_and_empty():
    client = NullYouTubeDataClient()
    assert client.enabled is False
    assert client.search_channel_ids("x", limit=3) == []
    assert client.search_video_ids("x", limit=3) == []
    assert client.resolve_channel_ids(["@a"]) == []
    assert client.uploads_playlist_id("UC") is None
    assert client.recent_video_ids("UU", limit=3) == []
    assert client.video_stats(["v"]) == []


def test_factory_returns_null_without_key(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    reset_settings_cache()
    assert isinstance(build_youtube_data_client(get_settings()), NullYouTubeDataClient)


def test_factory_returns_api_client_with_key(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "abc123")
    reset_settings_cache()
    client = build_youtube_data_client(get_settings())
    assert isinstance(client, ApiYouTubeDataClient)
    assert client.enabled is True
