"""Agent 4.5 — Pronunciation Director. Decides how the narrator SAYS each abbreviation in a script.

The static tables in ``providers.text_normalize`` are hand-verified and always win, but they can only
cover abbreviations somebody thought to add. Everything else falls to the default "spell it out" rule,
which is right for API and SDK and wrong for SWE, RBAC, REPL and ROME.

No table can fix that, because the answer is not in the spelling. "boosted NDCG, AUC, or MAP by five
percent" is a ranking metric said "em ay pee"; "opened the MAP view" is the ordinary word. Only the
surrounding sentence decides, so this asks a model once per run, with that sentence attached.

The model only CLASSIFIES. When it says "spell this one" the sounds come from ``spell_letters``, never
from the model -- so the common case cannot be got wrong by a bad generation. Best-effort throughout:
any failure returns {} and the curated tables carry the run exactly as before.
"""

from __future__ import annotations

import json
import re

from ..logging import get_logger
from ..prompts import load_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.text_normalize import acronyms_in, is_curated, spell_letters
from ..providers.tiering import TaskTier, select_model

# A spoken form is read aloud verbatim, so it must be ordinary letters and spaces. Anything else --
# digits, hyphens, slashes, phonetic marks -- is a sign the model answered in the wrong register and
# would be voiced literally.
_SAY_OK = re.compile(r"^[a-zA-Z ]{1,40}$")
# Enough to identify the usage without pasting the whole scene into the prompt.
_CONTEXT_CHARS = 90
# A script with more distinct abbreviations than this is not a script, and the prompt should not grow
# without bound.
_MAX_ITEMS = 40


def _context_for(text: str, acronym: str) -> str:
    """The words either side of the abbreviation's FIRST use -- the evidence the decision rests on."""
    m = re.search(rf"\b{re.escape(acronym)}\b", text)
    if not m:
        return ""
    start, end = max(0, m.start() - _CONTEXT_CHARS), min(len(text), m.end() + _CONTEXT_CHARS)
    return text[start:end].replace("\n", " ").strip()


def sanitize_say(value: object) -> str:
    """A model-supplied spoken form, or "" when it is not something safe to read aloud."""
    said = (value or "") if isinstance(value, str) else ""
    said = said.strip()
    return said if _SAY_OK.match(said) else ""


class PronunciationDirector:
    def __init__(self, settings, llm_provider: LLMProvider | None):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="pronunciation")

    def resolve(self, text: str, *, known: dict[str, str] | None = None) -> dict[str, str]:
        """``{"SWE": "swee", "SDK": "ess dee kay"}`` for the abbreviations in ``text``.

        ``known`` is anything already decided for this run (a hand-edited file from a previous pass);
        those are kept verbatim and never re-asked, so editing the file by hand actually sticks.
        """
        resolved = dict(known or {})
        if not self._settings.pronunciation_llm_enabled or self._llm is None:
            return resolved
        llm = self._llm  # narrowed here; narrowing does not survive the call into _decide
        # Curated entries are answered by the tables themselves, so asking about them would spend
        # tokens on a question whose answer is already fixed -- and invite a contradiction.
        pending = [a for a in acronyms_in(text) if not is_curated(a) and a not in resolved][
            :_MAX_ITEMS
        ]
        if not pending:
            return resolved
        try:
            decided = self._decide(llm, text, pending)
        except Exception as exc:
            # Deliberately broad. A live provider raises whatever its SDK feels like -- timeouts,
            # transport errors, quota objects -- and the promise this class makes is that a
            # pronunciation lookup can never be the reason a voiceover fails. Loud, but not fatal:
            # every abbreviation simply keeps the default spelling.
            self._log.warning("pronunciation_failed", error=str(exc), count=len(pending))
            return resolved
        resolved.update(decided)
        spoken = {k: v for k, v in decided.items() if v != spell_letters(k)}
        self._log.info(
            "pronunciation_resolved", asked=len(pending), as_word=len(spoken), words=sorted(spoken)
        )
        return resolved

    def _decide(self, llm: LLMProvider, text: str, pending: list[str]) -> dict[str, str]:
        payload = json.dumps(
            [{"abbreviation": a, "sentence": _context_for(text, a)} for a in pending],
            ensure_ascii=False,
        )
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        resp = llm.complete(
            f"Decide how to say each of these. Return ONLY the JSON now.\n\n{payload}",
            system=load_prompt("pronunciation.system"),
            # Zero: this is a lookup with a right answer, not a creative choice.
            temperature=0.0,
            max_tokens=self._settings.llm_max_tokens,
            model=model,
        )
        try:
            data = json.loads(resp.text.strip())
        except json.JSONDecodeError:
            data = json.loads(extract_json(resp.text))
        items = data.get("abbreviations") if isinstance(data, dict) else data
        wanted = set(pending)
        out: dict[str, str] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            name = (item.get("abbreviation") or "").strip().upper()
            if name not in wanted:
                continue  # never accept an abbreviation we did not ask about
            if (item.get("mode") or "").strip().lower() == "word":
                said = sanitize_say(item.get("say"))
                if said:
                    out[name] = said
                    continue
                self._log.warning("pronunciation_rejected", acronym=name, say=item.get("say"))
            # "letters", an unusable "say", or any other mode: spell it. The sounds come from the
            # letter table, so the model's job was only ever to CHOOSE, never to transcribe.
            out[name] = spell_letters(name)
        return out
