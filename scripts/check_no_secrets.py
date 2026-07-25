"""Guardrail: block credentials, personal media and channel-specific data from ever being committed.

Two layers share this module so they can never drift apart:

* the **pre-commit hook** (`.pre-commit-config.yaml`) runs it on the STAGED files, so a bad file is
  rejected BEFORE it becomes a commit (the only place a fix is still cheap);
* **`tests/unit/test_repo_hygiene.py`** re-runs it over every TRACKED file in the normal test gate,
  which also catches anything committed before the hook existed.

Run it by hand over the whole repo:  ``python scripts/check_no_secrets.py --all``
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# This file and its test state the patterns literally, so scanning them would self-trigger.
SELF_EXCLUDE = ("scripts/check_no_secrets.py", "tests/unit/test_repo_hygiene.py")

# Templates that LOOK like a forbidden file but are deliberately shipped (placeholders only).
ALLOWED_PATHS = (
    ".env.example",
    "data/affiliate_catalog.example.json",
    "src/content_foundry/data/env.example",
    "src/content_foundry/data/affiliate_catalog.example.json",
)

# Paths that must NEVER reach the public repo: operator credentials, personal biometrics
# (face/voice = deepfake material), private channel data, and generated runtime state.
FORBIDDEN_PATHS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|/)\.env$"), "a real .env with API keys (commit .env.example instead)"),
    (re.compile(r"(^|/)\.env\.(?!example$)[\w.]+$"), "a real .env variant"),
    (re.compile(r"^secrets/"), "OAuth client secrets / cached tokens"),
    (re.compile(r"(^|/)client_secret[^/]*\.json$"), "Google OAuth client secrets"),
    (re.compile(r"(^|/)[^/]*token[^/]*\.json$"), "a cached OAuth refresh token"),
    (re.compile(r"\.(pem|key|p12|pfx)$"), "a private key"),
    (re.compile(r"^output/"), "generated run output (published video ids, descriptions)"),
    (re.compile(r"\.(db|sqlite|sqlite3)$"), "a database file"),
    (re.compile(r"(^|/)avatar[^/]*\.(png|jpg|jpeg|webp)$"), "the operator's face photo"),
    (re.compile(r"\.cutout\.png$"), "a derived avatar cutout"),
    (re.compile(r"(^|/)voice_reference[^/]*"), "the operator's voice sample (voice-clone source)"),
    (re.compile(r"^Video_ideas\.txt$"), "the private content roadmap"),
    (re.compile(r"^Future_Plans\.txt$"), "private working notes"),
    (re.compile(r"^Reference\.md$"), "the private build log (real ASINs / affiliate tags)"),
    (
        re.compile(r"^data/affiliate_catalog\.json$"),
        "the operator's curated catalog (keep it local)",
    ),
)

# Real credential shapes. Deliberately long-enough to skip doc placeholders like `sk-ant-xxxxxxxx`.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("Google OAuth token", re.compile(r"ya29\.[0-9A-Za-z_-]{20,}")),
    ("Anthropic API key", re.compile(r"sk-ant-[0-9A-Za-z_-]{24,}")),
    ("OpenAI API key", re.compile(r"sk-(?:proj-)?[0-9A-Za-z_-]{32,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[0-9A-Za-z]{30,}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Telegram bot token", re.compile(r"\b\d{9,10}:AA[0-9A-Za-z_-]{32,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

# The operator's own monetization / channel identifiers. Tests must use neutral placeholders so the
# public repo never ties back to a specific channel.
CHANNEL_IDENTIFIERS: tuple[str, ...] = ("crackedstudio-20", "8jh38a", "BXmM", "7jL6")

_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".mp3",
        ".mp4",
        ".wav",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".whl",
        ".ttf",
        ".otf",
        ".onnx",
        ".safetensors",
        ".bin",
    }
)


def _norm(rel_path: str) -> str:
    """Repo-relative posix path.

    Deliberately avoids ``str.removeprefix`` (3.9+) and every other modern builtin: this hook runs
    on whichever ``python`` a contributor has on PATH, so it must not crash on an older one.
    (``lstrip('./')`` is also wrong here - it strips characters, turning ``.env`` into ``env``.)
    """
    rel = rel_path.replace("\\", "/")
    return rel[2:] if rel.startswith("./") else rel


def forbidden_path(rel_path: str) -> str:
    """The reason ``rel_path`` must not be committed, or ``""`` when it is fine."""
    rel = _norm(rel_path)
    if rel in ALLOWED_PATHS:
        return ""
    for pattern, reason in FORBIDDEN_PATHS:
        if pattern.search(rel):
            return reason
    return ""


def scan_text(rel_path: str, text: str) -> list[str]:
    """Credential / channel-identifier findings inside one file's text."""
    rel = _norm(rel_path)
    if rel in SELF_EXCLUDE:
        return []
    found = [
        f"{rel}: looks like a {label}" for label, pattern in SECRET_PATTERNS if pattern.search(text)
    ]
    # The live .env legitimately holds these, but it is never committed (blocked as a path above).
    found += [
        f"{rel}: contains the channel-specific identifier {ident!r} - use a neutral placeholder"
        for ident in CHANNEL_IDENTIFIERS
        if ident in text
    ]
    return found


def check_files(paths: list[str], *, root: Path) -> list[str]:
    """Every problem found in ``paths`` (forbidden locations + secret content)."""
    problems: list[str] = []
    for raw in paths:
        rel = _norm(raw)
        reason = forbidden_path(rel)
        if reason:
            problems.append(f"{rel}: must never be committed - {reason}")
            continue
        full = root / rel
        if not full.is_file() or full.suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            problems.extend(scan_text(rel, full.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return problems


def gitignore_problems(text: str) -> list[str]:
    """Pattern lines carrying a trailing inline ``#`` comment.

    Git has NO inline comments: ``/data/*   # keep local`` makes the WHOLE string the pattern, so the
    rule silently matches nothing. That exact bug is how personal files reached this public repo.
    """
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.search(r"\s#", line):
            problems.append(
                f".gitignore line {n}: inline comment disables the rule -> {stripped!r}"
            )
    return problems


def tracked_files(root: Path) -> list[str]:
    """Every file git tracks (i.e. everything that actually reaches the remote)."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files", nargs="*", help="files to check (pre-commit passes the staged ones)"
    )
    parser.add_argument("--all", action="store_true", help="check every git-tracked file instead")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    paths = tracked_files(root) if args.all or not args.files else args.files

    problems = check_files(paths, root=root)
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        problems += gitignore_problems(gitignore.read_text(encoding="utf-8"))

    if problems:
        print("Blocked - secrets or personal data would be committed:\n", file=sys.stderr)
        for problem in problems:
            print(f"  * {problem}", file=sys.stderr)
        print(
            "\nKeep the file locally and add it to .gitignore (comments on their OWN line).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
