"""End-screen recommendations (Ch. 13 sidecar).

The YouTube Data API CANNOT set end screens or cards (Studio-only). So for every published video we
instead record the two most topically-related PRIOR videos from this channel — their name + link — in
``end_screen.json``, and the operator sets the 1+1 end screen manually.

Pure + deterministic: the candidate pool is the LOCAL run history (so unlisted review uploads are
included — the read Data API would only see public videos), and the pick is by tag/title overlap,
never an LLM. Best-effort: any failure leaves publishing untouched.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[a-z0-9]+")
# Generic words that shouldn't drive "relatedness" (the niche is dropped separately at call time).
_STOP = frozenset(
    {
        "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "how", "why", "your",
        "you", "that", "this", "is", "are", "it", "vs", "video", "videos", "guide", "best", "top",
        "ways", "tips", "get", "make", "new", "2024", "2025", "2026", "2027",
    }
)


@dataclass(frozen=True)
class PastVideo:
    run_id: str
    title: str
    link: str
    privacy: str
    vocab: frozenset[str]


def _vocab(*texts: str) -> set[str]:
    words: set[str] = set()
    for text in texts:
        words |= {
            w for w in _WORD.findall((text or "").lower()) if len(w) > 2 and w not in _STOP
        }
    return words


def _link_for(pr: dict) -> str:
    url = (pr.get("video_url") or "").strip()
    if url:
        return url
    vid = (pr.get("youtube_video_id") or "").strip()
    return f"https://youtu.be/{vid}" if vid else ""


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def gather_past_videos(runs_dir, *, exclude_run_id: str, niche: str = "") -> list[PastVideo]:
    """Every prior run that actually reached YouTube (has a video id/link), newest ids last. Reads
    each run's ``publish_result.json`` (title + link + privacy) and ``script.json`` (tags)."""
    runs_dir = Path(runs_dir)
    drop = _vocab(niche)
    out: list[PastVideo] = []
    if not runs_dir.exists():
        return out
    for child in sorted((c for c in runs_dir.iterdir() if c.is_dir()), key=lambda p: p.name):
        if child.name == exclude_run_id:
            continue
        pr = _read_json(child / "publish_result.json")
        if not pr or not (pr.get("youtube_video_id") or "").strip():
            continue  # never uploaded
        link = _link_for(pr)
        if not link:
            continue
        title = (pr.get("chosen_title") or "").strip() or child.name
        tags = _read_json(child / "script.json").get("tags") or []
        vocab = frozenset(_vocab(title, " ".join(str(t) for t in tags)) - drop)
        out.append(
            PastVideo(
                run_id=child.name, title=title, link=link,
                privacy=(pr.get("privacy_status") or "").strip(), vocab=vocab,
            )
        )
    return out


def recommend(current_vocab: set[str], past_videos: list[PastVideo], *, count: int = 2) -> list[PastVideo]:
    """The ``count`` most related prior videos: most tag/title overlap first, newest as the tiebreak
    (so a channel with no overlap still backfills with the freshest uploads)."""
    ranked = sorted(
        past_videos,
        key=lambda p: (len(current_vocab & p.vocab), p.run_id),
        reverse=True,
    )
    return ranked[: max(0, count)]


def build_end_screen(
    runs_dir, *, run_id: str, title: str, tags, niche: str = "", count: int = 2
) -> dict:
    """The ``end_screen.json`` payload: the current video + its ``count`` recommended prior videos."""
    current_vocab = _vocab(title, " ".join(str(t) for t in (tags or []))) - _vocab(niche)
    recs = recommend(
        current_vocab, gather_past_videos(runs_dir, exclude_run_id=run_id, niche=niche), count=count
    )
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "for_video": title,
        "recommendations": [
            {"name": p.title, "link": p.link, "privacy": p.privacy, "run_id": p.run_id}
            for p in recs
        ],
    }
    if len(recs) < count:
        payload["note"] = (
            f"Only {len(recs)} prior published video(s) available (need {count}); this fills in as "
            "your catalog grows."
        )
    return payload


def write_end_screen(path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
