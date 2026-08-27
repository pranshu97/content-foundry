"""Bundle every local-only file into ONE restorable text file (for off-machine backup).

Most of this repo is on GitHub, but the files that make it *yours* deliberately are not: the API
keys, the OAuth tokens, your face and voice samples, your curated affiliate catalog and your private
notes. Lose the laptop and the public clone alone will not get you running again.

This writes a single self-contained text file you can drop in Drive, and reads it back:

    python scripts/backup_secrets.py                 # write secrets_backup.txt
    python scripts/backup_secrets.py --verify FILE   # check an old backup is still intact
    python scripts/backup_secrets.py --restore FILE  # write the files back onto a fresh machine

Binaries are base64-embedded so the backup is genuinely complete -- a backup that needs you to
*remember* to copy three more files by hand is a backup that fails on the day you need it. Pass
``--keys-only`` for a small text-only dump when you just want the credentials.

Every entry carries a sha256, so ``--verify`` proves the copy in Drive is still readable and
``--restore`` refuses to write a file that got mangled in transit.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "secrets_backup.txt"

# Everything here is gitignored on purpose. `binary` files get base64'd; the rest go in verbatim so
# the backup stays greppable. `required` marks what the pipeline cannot start without.
#
# Deliberately NOT backed up:
#   assets/avatar.cutout.png  - derived from avatar.png, regenerated on the next visuals run
#   data/content_foundry.db   - runtime state; `python scripts/init_db.py` rebuilds it empty
#   output/runs/**            - finished packages; back those up separately if you want them
TARGETS: tuple[tuple[str, bool, bool], ...] = (
    # (repo-relative path, is_binary, is_required)
    (".env", False, True),
    ("secrets/client_secrets.json", False, True),
    ("data/affiliate_catalog.json", False, False),
    ("assets/avatar.png", True, False),
    ("assets/voice_reference.wav", True, False),
    ("Future_Plans.txt", False, False),
    ("Video_ideas.txt", False, False),
    ("Reference.md", False, False),
)

# Cached OAuth tokens are per-channel and their names vary, so they are matched by glob.
TOKEN_GLOB = "secrets/token_*.json"

MARK_BEGIN = "===== BEGIN FILE "
MARK_END = "===== END FILE "
HEADER = "content-foundry local-file backup v1"


@dataclass(frozen=True)
class Entry:
    """One backed-up file: its repo-relative path, encoded payload and integrity data."""

    path: str
    encoding: str
    sha256: str
    size: int
    payload: str


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _encode(raw: bytes, *, binary: bool) -> tuple[str, str, bytes]:
    """Return ``(encoding, payload, canonical_bytes)``.

    Text files are stored verbatim so the backup can be read (and hand-edited) without this script.
    Anything binary -- or any text file that is not clean UTF-8 -- falls back to base64 rather than
    risking a lossy decode.

    Text payloads are canonicalised to LF first. The format is line-based, so a CRLF file would come
    back as LF no matter what we recorded, and a checksum taken over the original bytes would then
    fail on restore -- exactly the false alarm you do not want on a machine you are rebuilding.
    ``canonical_bytes`` is therefore what restore will actually write, and what gets digested.
    (Config, JSON and markdown do not care; binaries are never touched.)
    """
    if not binary:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            canonical = text.replace("\r\n", "\n").replace("\r", "\n")
            return "text", canonical, canonical.encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return "base64", "\n".join(b64[i : i + 76] for i in range(0, len(b64), 76)), raw


def _decode(entry: Entry) -> bytes:
    if entry.encoding == "text":
        return entry.payload.encode("utf-8")
    return base64.b64decode("".join(entry.payload.split()))


def collect(root: Path, *, keys_only: bool) -> tuple[list[Entry], list[str]]:
    """Read every target that exists. Returns ``(entries, notes)``; notes are for the operator."""
    wanted: list[tuple[str, bool, bool]] = list(TARGETS)
    wanted += [(p.relative_to(root).as_posix(), False, True) for p in sorted(root.glob(TOKEN_GLOB))]

    entries: list[Entry] = []
    notes: list[str] = []
    for rel, binary, required in wanted:
        if keys_only and binary:
            notes.append(f"skipped (--keys-only): {rel}")
            continue
        src = root / rel
        if not src.exists():
            notes.append(f"{'MISSING (required!)' if required else 'absent, skipped'}: {rel}")
            continue
        raw = src.read_bytes()
        encoding, payload, canonical = _encode(raw, binary=binary)
        entries.append(Entry(rel, encoding, _digest(canonical), len(canonical), payload))
    return entries, notes


def render(entries: list[Entry]) -> str:
    """Serialise entries into the backup format."""
    out = [
        HEADER,
        "",
        "Restore with:  python scripts/backup_secrets.py --restore <this file>",
        "Treat this file as a live credential. Never commit it; never share it.",
        "",
        f"files: {len(entries)}",
        "",
    ]
    for e in entries:
        out.append(f"{MARK_BEGIN}{e.path}")
        out.append(f"encoding: {e.encoding}")
        out.append(f"size: {e.size}")
        out.append(f"sha256: {e.sha256}")
        out.append("")
        out.append(e.payload)
        out.append(f"{MARK_END}{e.path}")
        out.append("")
    return "\n".join(out)


def parse(text: str) -> list[Entry]:
    """Read the backup format back into entries. Raises ``ValueError`` on a malformed file."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith(HEADER.rsplit(" v", 1)[0]):
        raise ValueError("not a content-foundry backup file")

    entries: list[Entry] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith(MARK_BEGIN):
            i += 1
            continue
        path = lines[i][len(MARK_BEGIN) :].strip()
        meta: dict[str, str] = {}
        i += 1
        while i < len(lines) and lines[i].strip():
            key, _, value = lines[i].partition(":")
            meta[key.strip()] = value.strip()
            i += 1
        i += 1  # the blank line separating metadata from payload
        body: list[str] = []
        closing = f"{MARK_END}{path}"
        while i < len(lines) and lines[i] != closing:
            body.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ValueError(f"unterminated entry for {path}")
        try:
            entries.append(
                Entry(path, meta["encoding"], meta["sha256"], int(meta["size"]), "\n".join(body))
            )
        except KeyError as exc:
            raise ValueError(f"entry {path} is missing {exc}") from exc
        i += 1
    if not entries:
        raise ValueError("backup file contains no entries")
    return entries


def verify(entries: list[Entry]) -> list[str]:
    """Names of entries whose payload no longer matches its recorded sha256."""
    bad = []
    for e in entries:
        try:
            raw = _decode(e)
        except Exception:  # noqa: BLE001 - any decode failure means the same thing: corrupt
            bad.append(e.path)
            continue
        if _digest(raw) != e.sha256 or len(raw) != e.size:
            bad.append(e.path)
    return bad


def restore(entries: list[Entry], root: Path, *, force: bool) -> tuple[list[str], list[str]]:
    """Write entries back to disk. Returns ``(written, skipped)``.

    Existing files are left alone unless ``--force``: restoring onto a working machine must not be
    the thing that destroys the one good copy of a token.
    """
    written: list[str] = []
    skipped: list[str] = []
    for e in entries:
        dest = root / e.path
        if dest.exists() and not force:
            skipped.append(e.path)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_decode(e))
        written.append(e.path)
    return written, skipped


def _human(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{n / 1024:.1f} KB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the backup")
    ap.add_argument("--restore", type=Path, metavar="FILE", help="restore files from a backup")
    ap.add_argument("--verify", type=Path, metavar="FILE", help="check a backup's checksums")
    ap.add_argument("--keys-only", action="store_true", help="skip the big binaries")
    ap.add_argument("--force", action="store_true", help="with --restore, overwrite existing files")
    args = ap.parse_args(argv)

    if args.verify:
        entries = parse(args.verify.read_text(encoding="utf-8"))
        bad = verify(entries)
        for e in entries:
            print(f"  {'CORRUPT' if e.path in bad else 'ok     '}  {e.path:44} {_human(e.size)}")
        print(f"\n{len(entries)} entries, {len(bad)} corrupt")
        return 1 if bad else 0

    if args.restore:
        entries = parse(args.restore.read_text(encoding="utf-8"))
        bad = verify(entries)
        if bad:
            print("Refusing to restore - these entries are corrupt:")
            for path in bad:
                print(f"  {path}")
            return 1
        written, skipped = restore(entries, ROOT, force=args.force)
        for path in written:
            print(f"  restored  {path}")
        for path in skipped:
            print(f"  kept existing (use --force to overwrite)  {path}")
        print(f"\n{len(written)} restored, {len(skipped)} left alone")
        return 0

    entries, notes = collect(ROOT, keys_only=args.keys_only)
    text = render(entries)
    args.out.write_text(text, encoding="utf-8")
    for note in notes:
        print(f"  {note}")
    for e in entries:
        print(f"  saved  {e.path:44} {_human(e.size):>9}  ({e.encoding})")
    print(f"\nWrote {args.out} - {len(entries)} files, {_human(len(text.encode('utf-8')))}")
    print("This file IS a credential. It is gitignored; keep it off the repo and out of chat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
