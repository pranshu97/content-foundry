"""`videos.update` REPLACES a snippet, so a category change must carry every other field across.

Getting this wrong wipes the title, description and tags off already-published videos, which is why
the logic is pure and tested rather than inlined into the maintenance script.
"""

from __future__ import annotations

import pytest

from content_foundry.providers.youtube import recategorized_snippet

# Shaped like a real videos.list snippet, read-only fields included.
LIVE = {
    "publishedAt": "2026-08-01T00:00:00Z",
    "channelId": "UC123",
    "channelTitle": "Example Channel",
    "title": "L3 vs L4 vs L5: How FAANG Actually Levels You",
    "description": "Long description with links\nand newlines.",
    "thumbnails": {"high": {"url": "https://i.ytimg.com/x.jpg"}},
    "tags": ["faang", "ml engineer", "levels"],
    "categoryId": "22",
    "liveBroadcastContent": "none",
    "defaultLanguage": "en",
    "defaultAudioLanguage": "en",
    "localized": {"title": "L3 vs L4 vs L5", "description": "..."},
}


def test_everything_the_viewer_sees_survives_a_category_change():
    """The whole risk in one assertion: only the category may differ."""
    out = recategorized_snippet(LIVE, "27")
    assert out is not None
    assert out["categoryId"] == "27"
    assert out["title"] == LIVE["title"]
    assert out["description"] == LIVE["description"]
    assert out["tags"] == LIVE["tags"]
    assert out["defaultLanguage"] == "en"
    assert out["defaultAudioLanguage"] == "en"


def test_read_only_fields_are_not_echoed_back():
    """Sending back a server-owned field is at best ignored and at worst rejected."""
    out = recategorized_snippet(LIVE, "27")
    assert out is not None
    for read_only in (
        "publishedAt",
        "channelId",
        "channelTitle",
        "thumbnails",
        "liveBroadcastContent",
        "localized",
    ):
        assert read_only not in out


def test_a_video_already_in_the_target_category_is_left_alone():
    """Returning None is what makes a re-run free AND stops it clobbering a later hand edit."""
    assert recategorized_snippet({**LIVE, "categoryId": "27"}, "27") is None
    assert recategorized_snippet({**LIVE, "categoryId": 27}, "27") is None  # API sends strings


def test_a_snippet_with_no_title_is_refused():
    """title is REQUIRED on update, and inventing one would rename a published video."""
    assert recategorized_snippet({"categoryId": "22"}, "27") is None
    assert recategorized_snippet({"title": "", "categoryId": "22"}, "27") is None


@pytest.mark.parametrize("junk", [None, "", [], 0, "snippet"])
def test_junk_input_is_refused_rather_than_guessed(junk):
    assert recategorized_snippet(junk, "27") is None


def test_a_video_missing_optional_fields_does_not_gain_empty_ones():
    """A video with no tags must not come back with tags=[] -- that is still a write."""
    out = recategorized_snippet({"title": "Bare", "categoryId": "22"}, "27")
    assert out == {"title": "Bare", "categoryId": "27"}
