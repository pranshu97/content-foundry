"""Sound-effects provider, script coercion, and no-op mixing (Ch. 11.6 / 12.4)."""

from __future__ import annotations

from pathlib import Path

from content_foundry.agents.script_generator import _str_or_none
from content_foundry.production.sound_design import mix_sfx
from content_foundry.providers.sfx import NullSfxClient, SfxLibrary

# The 10 sound effects shipped with the repo.
_SOUNDS = Path(__file__).resolve().parents[2] / "data" / "sounds"


def test_local_library_matches_common_keywords():
    lib = SfxLibrary(str(_SOUNDS))
    whoosh = lib.resolve("whoosh")
    assert whoosh and whoosh.endswith("whoosh.mp3")
    # Multi-word and partial keywords still land on the right file.
    assert "cash_register" in (lib.resolve("cash register") or "")
    assert "notification" in (lib.resolve("notification") or "")


def test_local_library_returns_none_without_match_or_key():
    lib = SfxLibrary(str(_SOUNDS))
    assert lib.resolve("zxqw_nonexistent_sound") is None
    assert lib.resolve("") is None


def test_local_library_missing_folder_is_safe(tmp_path):
    lib = SfxLibrary(str(tmp_path / "does_not_exist"))
    assert lib.resolve("whoosh") is None


def test_null_client_is_disabled_and_resolves_nothing():
    client = NullSfxClient()
    assert client.enabled is False
    assert client.resolve("whoosh") is None


def test_mix_sfx_is_noop_when_nothing_resolves(tmp_path):
    class _NoneClient:
        def resolve(self, keyword):
            return None

    out = tmp_path / "mixed.mp3"
    assert mix_sfx("narration.mp3", [(0.0, "whoosh")], _NoneClient(), out) is False
    assert not out.exists()


def test_mix_sfx_is_noop_with_empty_cues(tmp_path):
    class _Client:
        def resolve(self, keyword):
            return "x.mp3"

    out = tmp_path / "mixed.mp3"
    assert mix_sfx("narration.mp3", [], _Client(), out) is False
    assert not out.exists()


def test_mix_sfx_returns_false_on_undecodable_narration(tmp_path):
    # A mixing failure (e.g. a narration file pydub/ffmpeg can't decode) must NEVER crash the
    # render — it degrades to the plain narration by returning False.
    bad = tmp_path / "narration.mp3"
    bad.write_bytes(b"definitely not a real mp3 file")

    class _Client:
        def resolve(self, keyword):
            return str(_SOUNDS / "whoosh.mp3")

    out = tmp_path / "mixed.mp3"
    assert mix_sfx(str(bad), [(0.0, "whoosh")], _Client(), out) is False
    assert not out.exists()


def test_str_or_none_coercion():
    assert _str_or_none("whoosh") == "whoosh"
    assert _str_or_none("  ding  ") == "ding"
    assert _str_or_none("") is None
    assert _str_or_none("   ") is None
    assert _str_or_none(None) is None
    assert _str_or_none(["whoosh"]) is None  # lists are ignored, not crashed on
