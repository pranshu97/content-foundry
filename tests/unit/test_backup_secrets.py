"""The off-machine backup must round-trip byte-for-byte, and must never be committable.

A backup is only worth the restore. These tests exercise the restore path -- including the failure
modes that matter on the day you actually need it: a file that got mangled in transit, and a restore
run against a machine that still has good copies.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name: str):
    """Load a scripts/*.py file by path (the scripts dir is not an importable package).

    The module has to be registered in ``sys.modules`` BEFORE it executes: ``@dataclass`` resolves
    annotations via ``sys.modules[cls.__module__]`` and blows up on a module that is not there yet.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backup = _load("backup_secrets")
guard = _load("check_no_secrets")


def _entries(root: Path) -> list[backup.Entry]:
    return backup.collect(root, keys_only=False)[0]


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A miniature repo holding one of each kind of local-only file."""
    (tmp_path / "secrets").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "assets").mkdir()
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=abc\nVIDEO_SPEED=1.08\n", encoding="utf-8")
    (tmp_path / "secrets/client_secrets.json").write_text('{"web": {}}', encoding="utf-8")
    (tmp_path / "secrets/token_someone.json").write_text('{"token": "x"}', encoding="utf-8")
    (tmp_path / "assets/avatar.png").write_bytes(bytes(range(256)) * 4)
    (tmp_path / "assets/voice_reference.wav").write_bytes(b"RIFF\x00\xff\xfe" * 100)
    return tmp_path


def test_a_text_file_is_stored_verbatim_so_the_backup_stays_readable(fake_repo: Path) -> None:
    env = next(e for e in _entries(fake_repo) if e.path == ".env")
    assert env.encoding == "text"
    assert "ANTHROPIC_API_KEY=abc" in backup.render([env])


def test_binaries_round_trip_byte_for_byte(fake_repo: Path) -> None:
    original = (fake_repo / "assets/avatar.png").read_bytes()
    entry = next(e for e in _entries(fake_repo) if e.path == "assets/avatar.png")
    assert entry.encoding == "base64"
    assert backup._decode(entry) == original


def test_the_whole_bundle_survives_a_render_parse_cycle(fake_repo: Path) -> None:
    before = _entries(fake_repo)
    after = backup.parse(backup.render(before))
    assert {e.path: backup._decode(e) for e in after} == {e.path: backup._decode(e) for e in before}


def test_oauth_tokens_are_picked_up_by_glob_whatever_the_channel_is_called(
    fake_repo: Path,
) -> None:
    assert "secrets/token_someone.json" in {e.path for e in _entries(fake_repo)}


def test_keys_only_skips_the_big_binaries(fake_repo: Path) -> None:
    entries, notes = backup.collect(fake_repo, keys_only=True)
    assert not [e for e in entries if e.encoding == "base64"]
    assert any("avatar.png" in n for n in notes)


def test_a_missing_required_file_is_called_out_loudly(tmp_path: Path) -> None:
    _, notes = backup.collect(tmp_path, keys_only=False)
    assert any(n.startswith("MISSING (required!)") and ".env" in n for n in notes)


def test_corruption_in_transit_is_detected(fake_repo: Path) -> None:
    entries = backup.parse(backup.render(_entries(fake_repo)))
    good = [e for e in entries if e.path == ".env"][0]
    tampered = backup.Entry(good.path, good.encoding, good.sha256, good.size, "TAMPERED=1")
    assert backup.verify([tampered]) == [".env"]
    assert backup.verify(entries) == []


def test_restore_writes_the_files_back(fake_repo: Path, tmp_path: Path) -> None:
    entries = backup.parse(backup.render(_entries(fake_repo)))
    dest = tmp_path / "fresh"
    written, skipped = backup.restore(entries, dest, force=False)

    assert not skipped
    assert (dest / ".env").read_text(encoding="utf-8") == (fake_repo / ".env").read_text(
        encoding="utf-8"
    )
    assert (dest / "assets/avatar.png").read_bytes() == (
        fake_repo / "assets/avatar.png"
    ).read_bytes()
    assert set(written) == {e.path for e in entries}


def test_restore_will_not_clobber_a_working_machine_by_default(fake_repo: Path) -> None:
    entries = backup.parse(backup.render(_entries(fake_repo)))
    (fake_repo / ".env").write_text("NEWER=1", encoding="utf-8")

    written, skipped = backup.restore(entries, fake_repo, force=False)

    assert ".env" in skipped
    assert not written
    assert (fake_repo / ".env").read_text(encoding="utf-8") == "NEWER=1"


def test_force_overwrites(fake_repo: Path) -> None:
    entries = backup.parse(backup.render(_entries(fake_repo)))
    (fake_repo / ".env").write_text("NEWER=1", encoding="utf-8")

    written, skipped = backup.restore(entries, fake_repo, force=True)

    assert ".env" in written and not skipped
    assert "ANTHROPIC_API_KEY=abc" in (fake_repo / ".env").read_text(encoding="utf-8")


def test_restoring_a_corrupt_backup_aborts_before_writing_anything(
    fake_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "b.txt"
    path.write_text(backup.render(_entries(fake_repo)).replace("ANTHROPIC", "XNTHROPIC"), "utf-8")

    assert backup.main(["--restore", str(path)]) == 1
    assert "Refusing to restore" in capsys.readouterr().out


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello world",
        "content-foundry local-file backup v1\n\n===== BEGIN FILE a\nencoding: text",
    ],
)
def test_a_file_that_is_not_a_backup_is_rejected_rather_than_half_read(text: str) -> None:
    with pytest.raises(ValueError):
        backup.parse(text)


def test_a_windows_crlf_file_still_verifies_after_a_round_trip(tmp_path: Path) -> None:
    """The format is line-based, so CRLF cannot survive it -- the checksum must agree.

    Found the hard way: digesting the ORIGINAL bytes made every CRLF file (i.e. most files written
    on the operator's own machine, starting with .env) report itself as corrupt on restore.
    """
    (tmp_path / ".env").write_bytes(b"ANTHROPIC_API_KEY=abc\r\nVIDEO_SPEED=1.08\r\n")

    entries = backup.parse(backup.render(_entries(tmp_path)))

    assert backup.verify(entries) == []
    assert backup._decode(entries[0]) == b"ANTHROPIC_API_KEY=abc\nVIDEO_SPEED=1.08\n"


def test_a_binary_with_crlf_bytes_in_it_is_never_newline_mangled(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    payload = b"RIFF\r\n\x00\rmore\r\n"
    (tmp_path / "assets/avatar.png").write_bytes(payload)

    entries = backup.parse(backup.render(_entries(tmp_path)))

    assert backup._decode(entries[0]) == payload


def test_the_backup_file_can_never_be_committed() -> None:
    for name in ("secrets_backup.txt", "secrets_backup_2026.txt"):
        assert guard.forbidden_path(name), f"{name} must be blocked by the pre-commit guard"


def test_git_actually_ignores_the_backup_file() -> None:
    """The guard is the second line of defence; gitignore is the first.

    A pattern with an inline ``#`` comment silently does nothing, so assert against real git rather
    than eyeballing .gitignore.
    """
    done = subprocess.run(
        ["git", "check-ignore", "-v", "--no-index", "--", "secrets_backup.txt"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, "secrets_backup.txt is NOT gitignored"
    assert "secrets_backup" in done.stdout, f"matched by the wrong rule: {done.stdout!r}"
