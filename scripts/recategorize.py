"""One-off maintenance: put every existing video on the channel into a single YouTube category.

Future uploads already use YOUTUBE_CATEGORY_ID, so this only exists to backfill what is already
published. DRY RUN BY DEFAULT -- pass --apply to actually write.

    python scripts/recategorize.py                # show what would change
    python scripts/recategorize.py --apply        # do it

Why this is not a one-liner: ``videos.update`` REPLACES the snippet rather than patching it, and the
API marks ``snippet.title`` and ``snippet.categoryId`` as required. Sending just the category would
strip the title, description and tags off every published video. ``recategorized_snippet`` does the
read-modify-write and is unit-tested; this file is only the I/O around it.

NOT SETTABLE HERE: the "Type" field that Studio shows underneath the Education category. It appears
nowhere in the v3 `videos` resource -- not in snippet, status, contentDetails or topicDetails -- so
like end screens, cards and comment pinning it is Studio-only and has to be set by hand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from content_foundry.config import get_settings  # noqa: E402
from content_foundry.providers import build_publisher  # noqa: E402
from content_foundry.providers.youtube import recategorized_snippet  # noqa: E402


def uploads_playlist_id(service) -> str:
    resp = service.channels().list(part="contentDetails", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        raise SystemExit("No channel found for these credentials.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def all_video_ids(service, playlist_id: str) -> list[str]:
    ids, page = [], None
    while True:
        resp = (
            service.playlistItems()
            .list(part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=page)
            .execute()
        )
        ids += [i["contentDetails"]["videoId"] for i in resp.get("items", [])]
        page = resp.get("nextPageToken")
        if not page:
            return ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    ap.add_argument("--category", default="", help="category id (default: YOUTUBE_CATEGORY_ID)")
    args = ap.parse_args()

    settings = get_settings()
    target = args.category or settings.youtube_category_id
    service = build_publisher(settings).service

    ids = all_video_ids(service, uploads_playlist_id(service))
    print(f"channel has {len(ids)} uploads | target category {target}\n")

    changed = skipped = failed = 0
    for start in range(0, len(ids), 50):
        batch = ids[start : start + 50]
        resp = service.videos().list(part="snippet", id=",".join(batch)).execute()
        for item in resp.get("items", []):
            snippet = item.get("snippet") or {}
            title = (snippet.get("title") or "")[:58]
            now = snippet.get("categoryId")
            updated = recategorized_snippet(snippet, target)
            if updated is None:
                print(f"  ok    [{now:>2}] {title}")
                skipped += 1
                continue
            if not args.apply:
                print(f"  WOULD [{now:>2} -> {target}] {title}")
                changed += 1
                continue
            try:
                service.videos().update(
                    part="snippet", body={"id": item["id"], "snippet": updated}
                ).execute()
                print(f"  DONE  [{now:>2} -> {target}] {title}")
                changed += 1
            except Exception as exc:  # keep going; one failure must not strand the rest
                print(f"  FAIL  {title}: {exc}")
                failed += 1

    verb = "changed" if args.apply else "would change"
    print(f"\n{verb} {changed} | already correct {skipped} | failed {failed}")
    if changed and not args.apply:
        print("re-run with --apply to write these.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
