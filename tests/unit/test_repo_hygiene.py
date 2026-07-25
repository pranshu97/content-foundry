"""Repository hygiene: the public repo and the published package must stay free of personal data.

These are regression tests for real leaks: the operator's face/voice, the private content roadmap and
the curated affiliate catalog were once tracked, because `.gitignore` rules carried trailing inline
comments (which git does NOT support, so the rules silently matched nothing).

The same guard runs earlier as a pre-commit hook; this suite is the backstop that also catches
anything already committed, and it proves the guard itself still works.
"""

from __future__ import annotations

import importlib.util
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_guard():
    """Load scripts/check_no_secrets.py (not importable as a package) by path."""
    spec = importlib.util.spec_from_file_location(
        "check_no_secrets", ROOT / "scripts" / "check_no_secrets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _tracked_files() -> list[str]:
    """Files git actually tracks, or skip when the checkout/git is unavailable."""
    try:
        return guard.tracked_files(ROOT)
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - sdist / no git
        pytest.skip("not a git checkout")


# ----------------------------------------------------------------- the repo itself stays clean
def test_no_tracked_file_holds_secrets_or_personal_data() -> None:
    problems = guard.check_files(_tracked_files(), root=ROOT)
    assert problems == [], "personal data or credentials are tracked:\n" + "\n".join(problems)


def test_gitignore_rules_are_not_disabled_by_inline_comments() -> None:
    """The exact bug that leaked files: `/data/*  # keep local` matches nothing."""
    problems = guard.gitignore_problems((ROOT / ".gitignore").read_text(encoding="utf-8"))
    assert problems == [], "\n".join(problems)


def _is_ignored(path: str) -> bool:
    """Ask git itself whether the rule works — substring-matching .gitignore proves nothing.

    ``check-ignore`` exits 0 whenever a pattern *matches*, including a ``!`` re-include, so the
    verdict comes from the winning pattern rather than the exit code.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-v", "--no-index", "--", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 1:  # no pattern matched at all
        return False
    if result.returncode != 0:  # pragma: no cover - no git checkout
        pytest.skip("not a git checkout")
    pattern = result.stdout.split("\t", 1)[0].split(":", 2)[2]
    return not pattern.startswith("!")


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "secrets/token_channel.json",
        "output/runs/0018/metadata.json",
        "data/affiliate_catalog.json",
        "assets/avatar.png",
        "assets/avatar.cutout.png",
        "assets/voice_reference.wav",
        "Video_ideas.txt",
        "content.db",
    ],
)
def test_gitignore_actually_ignores_every_sensitive_path(path: str) -> None:
    """Being ignored is what keeps these local-only; a broken rule must fail loudly."""
    assert _is_ignored(path), f"{path} is NOT ignored — it can be committed by accident"


@pytest.mark.parametrize("path", [".env.example", "data/affiliate_catalog.example.json"])
def test_gitignore_still_lets_the_templates_through(path: str) -> None:
    """Over-broad rules would silently drop the templates a new operator needs."""
    assert not _is_ignored(path), f"{path} is ignored but must ship"


# ------------------------------------------------------------------- the guard itself still works
@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "secrets/token_channel.json",
        "assets/avatar.png",
        "assets/avatar.cutout.png",
        "assets/voice_reference.wav",
        "Video_ideas.txt",
        "data/affiliate_catalog.json",
        "output/runs/0018/metadata.json",
        "content.db",
        "keys/server.pem",
    ],
)
def test_guard_rejects_forbidden_paths(path: str) -> None:
    assert guard.forbidden_path(path), f"{path} should be blocked"


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "data/affiliate_catalog.example.json",
        "src/content_foundry/data/env.example",
        "src/content_foundry/production/affiliate.py",
        "README.md",
    ],
)
def test_guard_allows_templates_and_source(path: str) -> None:
    assert guard.forbidden_path(path) == "", f"{path} must stay committable"


@pytest.mark.parametrize(
    "secret",
    [
        "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r",
        "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_guard_detects_real_credential_shapes(secret: str) -> None:
    assert guard.scan_text("config.py", f"KEY = '{secret}'"), f"missed {secret[:12]}..."


def test_guard_ignores_documentation_placeholders() -> None:
    """Docs are full of fake keys; flagging them would train everyone to skip the hook."""
    docs = "ANTHROPIC_API_KEY=sk-ant-xxxxxxxx\nOPENAI_API_KEY=sk-xxxxxxxx\nAMAZON_TAG=assoctag-20"
    assert guard.scan_text("src/content_foundry/data/env.example", docs) == []


def test_guard_flags_channel_specific_identifiers() -> None:
    """The repo must stay generic: no real affiliate tags, even in tests."""
    assert guard.scan_text("tests/unit/test_affiliate.py", "tag = 'crackedstudio-20'")


# ------------------------------------------------------------------------ the package stays clean
def test_published_package_ships_the_operator_templates() -> None:
    """A fresh install needs these to configure anything (Ch. 6)."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    bundled = data["tool"]["setuptools"]["package-data"]["content_foundry"]
    for template in ("data/env.example", "data/affiliate_catalog.example.json"):
        assert template in bundled, f"{template} is missing from the wheel"
        assert (ROOT / "src" / "content_foundry" / template).is_file()


def test_published_package_ships_no_personal_data() -> None:
    """Anything under src/ goes to PyPI, where a leak cannot be recalled."""
    packaged = [str(p.relative_to(ROOT)) for p in (ROOT / "src").rglob("*") if p.is_file()]
    problems = guard.check_files(packaged, root=ROOT)
    assert problems == [], "the wheel would leak:\n" + "\n".join(problems)
