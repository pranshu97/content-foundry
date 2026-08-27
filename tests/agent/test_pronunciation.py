"""Agent 4.5 (Pronunciation Director): the sentence decides, and curated tables are never overruled."""

from __future__ import annotations

import json

import pytest

from content_foundry.agents.pronunciation import PronunciationDirector, sanitize_say
from content_foundry.config import get_settings, reset_settings_cache
from content_foundry.providers.text_normalize import (
    acronyms_in,
    is_curated,
    speechify_numbers,
    spell_letters,
)


class _LLM:
    """Returns one canned reply and records what it was asked."""

    def __init__(self, reply: str = "", raises: bool = False):
        self._reply = reply
        self._raises = raises
        self.prompts: list[str] = []

    def complete(self, prompt, *, system="", temperature=0.0, max_tokens=0, model=""):
        self.prompts.append(prompt)
        if self._raises:
            raise RuntimeError("provider down")
        return type("R", (), {"text": self._reply})()


def _settings(monkeypatch):
    monkeypatch.setenv("PRONUNCIATION_LLM_ENABLED", "true")
    reset_settings_cache()
    return get_settings()


def _reply(*pairs):
    return json.dumps(
        {
            "abbreviations": [
                dict(zip(("abbreviation", "mode", "say"), p, strict=False)) for p in pairs
            ]
        }
    )


def _asked(llm: _LLM) -> list[str]:
    """The abbreviations the model was asked to DECIDE, as opposed to ones merely quoted in a
    context sentence -- the distinction the prompt is built on."""
    payload = json.loads(llm.prompts[0].split("\n\n", 1)[1])
    return [item["abbreviation"] for item in payload]


def test_the_word_like_abbreviations_get_a_spoken_form(monkeypatch):
    llm = _LLM(_reply(("SWE", "word", "swee"), ("RBAC", "word", "ar back"), ("SDK", "letters")))
    out = PronunciationDirector(_settings(monkeypatch), llm).resolve(
        "Look at SWE-bench. The agent inherits RBAC permissions. We enforce lazy SDK imports."
    )
    assert out["SWE"] == "swee"
    assert out["RBAC"] == "ar back"
    # "letters" is answered by spell_letters, NOT by the model -- so the common case cannot be
    # broken by a bad generation, and it inherits the spaced-capitals rendering automatically.
    assert out["SDK"] == "S D K" == spell_letters("SDK")


def test_the_sentence_is_what_the_model_is_given(monkeypatch):
    """The whole point: MAP is a metric beside NDCG and a word elsewhere, and only the line says so."""
    llm = _LLM(_reply(("MAP", "letters")))
    PronunciationDirector(_settings(monkeypatch), llm).resolve(
        "Telling a hiring team you boosted offline metrics like NDCG, AUC, or MAP by five percent."
    )
    asked = llm.prompts[0]
    assert "MAP" in asked
    assert "NDCG" in asked  # the neighbouring metrics are the evidence


def test_a_curated_acronym_is_never_asked_about(monkeypatch):
    """FAANG and MLE were settled by ear. A model must not get the chance to contradict them.

    They may still APPEAR in the prompt inside a neighbour's context sentence -- that is the evidence
    the neighbour is judged on -- so the property is about what is asked, not what is mentioned.
    """
    llm = _LLM(_reply(("SWE", "word", "swee")))
    PronunciationDirector(_settings(monkeypatch), llm).resolve(
        "FAANG hires an MLE to own the LLM stack, and the SWE ships it."
    )
    assert _asked(llm) == ["SWE"]
    for settled in ("FAANG", "MLE", "LLM"):
        assert is_curated(settled)


def test_a_curated_answer_still_wins_even_if_an_override_disagrees():
    """Belt and braces: the override map is consulted AFTER both curated tables."""
    hostile = {"MLE": "mush", "FAANG": "eff ay ay en gee"}
    out = speechify_numbers("The MLE at FAANG shipped it.", hostile)
    assert "M L E" in out
    assert "FAANG" in out
    assert "mush" not in out


def test_an_override_fills_a_gap_the_tables_leave_open():
    out = speechify_numbers(
        "Look at SWE-bench and the RBAC rules.", {"SWE": "swee", "RBAC": "ar back"}
    )
    assert "swee-bench" in out
    assert "ar back" in out
    assert "double you" not in out  # the exact thing the operator reported


def test_an_override_pluralises_the_way_a_person_says_it():
    assert "swees" in speechify_numbers("Two SWEs joined.", {"SWE": "swee"})


@pytest.mark.parametrize(
    "bad", ["", "   ", "s-w-e", "sw33", "ˈswiː", "say/it", "x" * 41, None, 12, {"a": 1}]
)
def test_an_unusable_spoken_form_falls_back_to_spelling(monkeypatch, bad):
    """A model answering in the wrong register (phonetic marks, hyphens, digits) would be read out
    literally, which is worse than the spelling it replaced."""
    assert sanitize_say(bad) == ""
    llm = _LLM(json.dumps({"abbreviations": [{"abbreviation": "SWE", "mode": "word", "say": bad}]}))
    out = PronunciationDirector(_settings(monkeypatch), llm).resolve("The SWE shipped it.")
    assert out["SWE"] == spell_letters("SWE")


@pytest.mark.parametrize(
    "reply", ["not json", "{}", '{"abbreviations": null}', '{"abbreviations": [{"x": 1}]}', "[]"]
)
def test_an_unusable_reply_leaves_every_abbreviation_on_the_default(monkeypatch, reply):
    out = PronunciationDirector(_settings(monkeypatch), _LLM(reply)).resolve("The SWE shipped it.")
    assert "SWE" not in out or out["SWE"] == spell_letters("SWE")


def test_a_dead_provider_is_not_fatal(monkeypatch):
    out = PronunciationDirector(_settings(monkeypatch), _LLM(raises=True)).resolve(
        "The SWE shipped."
    )
    assert out == {}


def test_an_abbreviation_we_did_not_ask_about_is_ignored(monkeypatch):
    """A model that invents entries must not be able to redefine words elsewhere in the video."""
    llm = _LLM(_reply(("SWE", "word", "swee"), ("GPU", "word", "goopoo")))
    out = PronunciationDirector(_settings(monkeypatch), llm).resolve("The SWE shipped it.")
    assert out == {"SWE": "swee"}


def test_a_hand_edited_entry_is_kept_and_never_re_asked(monkeypatch):
    """The map is written to the run so it can be corrected; a correction has to survive a re-voice."""
    llm = _LLM(_reply(("RBAC", "letters")))
    out = PronunciationDirector(_settings(monkeypatch), llm).resolve(
        "The SWE set the RBAC rules.", known={"SWE": "swee by hand"}
    )
    assert out["SWE"] == "swee by hand"
    assert _asked(llm) == ["RBAC"]


def test_disabled_or_no_llm_changes_nothing(monkeypatch):
    monkeypatch.setenv("PRONUNCIATION_LLM_ENABLED", "false")
    reset_settings_cache()
    llm = _LLM(_reply(("SWE", "word", "swee")))
    assert PronunciationDirector(get_settings(), llm).resolve("The SWE shipped.") == {}
    assert llm.prompts == []
    assert PronunciationDirector(_settings(monkeypatch), None).resolve("The SWE shipped.") == {}


def test_acronyms_in_matches_what_actually_gets_transformed():
    """If these two ever disagree we would be asking about one set and rewriting another."""
    text = "SWE-bench, RBAC, APIs and FAANG, plus SDE2 and a lowercase api."
    found = acronyms_in(text)
    assert found == ["SWE", "RBAC", "API", "FAANG"]
    assert "api" not in found  # lowercase is left alone
    # SDE2 is absent on purpose: `spell_designations` runs first and has already turned it into
    # "ess dee ee two", so by the time the spelling pass runs there is no capital run left to ask
    # about. Asking would invite a second, conflicting answer for the same token.
    assert "SDE" not in found
