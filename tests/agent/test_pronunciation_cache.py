"""The cached pronunciation map must not outlive the rules that produced it.

Run 0030 is the reason this exists: after the spaced-capitals change its cached map still said
`AI` -> "ay eye", and because a cached entry is deliberately never re-asked, re-voicing would have
fed the broken pronunciation straight back with a green PASS and nothing to see.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from content_foundry.agents.voiceover import Voiceover
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.providers.text_normalize import RULES_VERSION

REL = "assets/pronunciations.json"


def _script():
    """Only ``scenes[*].narration`` is read, so a stub keeps this test about the cache alone."""
    return SimpleNamespace(scenes=[SimpleNamespace(narration="The AI team ships the API.")])


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("PRONUNCIATION_LLM_ENABLED", "false")  # no LLM: isolate the cache logic
    reset_settings_cache()
    return Voiceover(get_settings(), tts_provider=None, llm_provider=None)


def _write(tmp_path, payload):
    p = tmp_path / REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_a_map_written_under_the_current_rules_is_reused(agent, tmp_path):
    """The whole point of caching: a hand correction has to survive an ordinary re-voice."""
    _write(tmp_path, {"rules_version": RULES_VERSION, "pronunciations": {"AI": "hand edited"}})
    assert agent._pronunciations(_script(), tmp_path) == {"AI": "hand edited"}


def test_a_map_from_older_rules_is_discarded(agent, tmp_path):
    """The run-0030 failure. Stale renderings must not be handed back to the voice."""
    _write(tmp_path, {"rules_version": RULES_VERSION - 1, "pronunciations": {"AI": "ay eye"}})
    assert agent._pronunciations(_script(), tmp_path) == {}


def test_the_old_unstamped_format_is_discarded(agent, tmp_path):
    """Every map written before this guard existed is, by definition, from the old rules."""
    _write(tmp_path, {"AI": "ay eye", "API": "ay pee eye"})
    assert agent._pronunciations(_script(), tmp_path) == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"rules_version": RULES_VERSION},  # no entries
        {"rules_version": RULES_VERSION, "pronunciations": "not a dict"},
        {"pronunciations": {"AI": "A I"}},  # no version
        [],
    ],
)
def test_a_malformed_map_is_ignored_rather_than_fatal(agent, tmp_path, payload):
    assert agent._pronunciations(_script(), tmp_path) == {}


def test_a_missing_file_is_fine(agent, tmp_path):
    assert agent._pronunciations(_script(), tmp_path) == {}


def test_what_gets_written_carries_the_stamp(agent, tmp_path, monkeypatch):
    """A map written without the stamp would be discarded on the very next run."""
    monkeypatch.setenv("PRONUNCIATION_LLM_ENABLED", "true")
    reset_settings_cache()

    class _LLM:
        def complete(self, prompt, *, system="", temperature=0.0, max_tokens=0, model=""):
            body = {"abbreviations": [{"abbreviation": "AI", "mode": "letters"}]}
            return type("R", (), {"text": json.dumps(body)})()

    out = Voiceover(get_settings(), None, _LLM())._pronunciations(_script(), tmp_path)
    assert out["AI"] == "A I"
    saved = json.loads((tmp_path / REL).read_text(encoding="utf-8"))
    assert saved["rules_version"] == RULES_VERSION
    assert saved["pronunciations"]["AI"] == "A I"
