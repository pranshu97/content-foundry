"""End-screen recommendations: topical pick from run history, name+link payload, best-effort I/O."""

from __future__ import annotations

import json
from pathlib import Path

from content_foundry.production.end_screen import (
    build_end_screen,
    gather_past_videos,
    recommend,
    write_end_screen,
)


def _make_run(runs: Path, run_id: str, *, title: str, tags, video_id="", url="", privacy="public"):
    d = runs / run_id
    d.mkdir(parents=True, exist_ok=True)
    pr = {
        "chosen_title": title, "youtube_video_id": video_id, "video_url": url,
        "privacy_status": privacy,
    }
    (d / "publish_result.json").write_text(json.dumps(pr), encoding="utf-8")
    (d / "script.json").write_text(json.dumps({"tags": tags}), encoding="utf-8")
    return d


def test_gather_skips_unpublished_and_excludes_current(tmp_path):
    runs = tmp_path / "runs"
    _make_run(runs, "0001", title="System Design Interview Guide", tags=["system design"], video_id="AAA")
    _make_run(runs, "0002", title="Never uploaded", tags=["x"], video_id="")  # no id -> skipped
    _make_run(runs, "0003", title="Current one", tags=["y"], video_id="CCC")
    past = gather_past_videos(runs, exclude_run_id="0003", niche="tech careers")
    assert {p.run_id for p in past} == {"0001"}  # 0002 no id, 0003 is the current run
    assert past[0].link == "https://youtu.be/AAA"


def test_recommend_prefers_topical_overlap_then_recency(tmp_path):
    runs = tmp_path / "runs"
    _make_run(runs, "0001", title="Cooking pasta", tags=["cooking"], video_id="A")
    _make_run(runs, "0002", title="System Design Interview", tags=["system design", "interview"], video_id="B")
    _make_run(runs, "0005", title="Old unrelated", tags=["gardening"], video_id="E")
    past = gather_past_videos(runs, exclude_run_id="9999", niche="tech careers")
    recs = recommend({"system", "design", "interview"}, past, count=2)
    assert recs[0].run_id == "0002"  # strongest overlap wins
    assert len(recs) == 2
    assert recs[1].run_id == "0005"  # newest of the zero-overlap rest (recency tiebreak)


def test_build_end_screen_payload_shape_and_note(tmp_path):
    runs = tmp_path / "runs"
    _make_run(runs, "0001", title="System Design Interview", tags=["system design"],
              video_id="B", privacy="unlisted")
    payload = build_end_screen(
        runs, run_id="0002", title="How to ace the system design interview",
        tags=["system design", "faang"], niche="tech careers", count=2,
    )
    assert payload["for_video"].startswith("How to ace")
    assert len(payload["recommendations"]) == 1
    rec = payload["recommendations"][0]
    assert rec == {"name": "System Design Interview", "link": "https://youtu.be/B",
                   "privacy": "unlisted", "run_id": "0001"}
    assert "note" in payload  # only 1 available, needed 2


def test_video_url_preferred_over_id(tmp_path):
    runs = tmp_path / "runs"
    _make_run(runs, "0001", title="X", tags=["a"], video_id="ID1", url="https://youtu.be/CUSTOM")
    past = gather_past_videos(runs, exclude_run_id="9", niche="")
    assert past[0].link == "https://youtu.be/CUSTOM"


def test_write_end_screen_roundtrip(tmp_path):
    out = tmp_path / "runs" / "0002" / "end_screen.json"
    write_end_screen(out, {"schema_version": "1.0", "recommendations": []})
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == "1.0"


def test_missing_runs_dir_is_empty(tmp_path):
    assert gather_past_videos(tmp_path / "nope", exclude_run_id="0001") == []


def test_recommendations_comment_formats_links_or_empty():
    from content_foundry.production.end_screen import recommendations_comment

    body = recommendations_comment(
        [
            {"name": "System Design Interview", "link": "https://youtu.be/B"},
            {"name": "Coding Interview Tips", "link": "https://youtu.be/C"},
        ],
        header="Watch next:",
    )
    assert body.startswith("Watch next:")
    assert "System Design Interview: https://youtu.be/B" in body
    assert "https://youtu.be/C" in body
    # Nothing to recommend / a link-less entry -> empty, so an empty comment is never posted:
    assert recommendations_comment([]) == ""
    assert recommendations_comment([{"name": "x", "link": ""}]) == ""
    # A blank header falls back to a sensible default line.
    assert recommendations_comment([{"name": "X", "link": "https://youtu.be/Z"}]).startswith(
        "More videos you might like:"
    )

