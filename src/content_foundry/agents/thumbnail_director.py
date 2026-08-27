"""Agent 5.6 — Thumbnail Director. Asks the LLM for ONE text-to-image prompt from the video's
DESCRIPTION, driven by a single high-quality worked EXAMPLE (a description->prompt pair in
``thumbnail_director.system.txt``) so the output matches that cinematic caliber — no rulebook, no
presenter/avatar details. The draft is then REFINED in place for up to ``thumbnail_refine_turns``
rounds (``thumbnail_refine.system.txt``): the thumbnail is the highest-leverage frame in the whole
video, and a single sample at temperature 0.9 is a coin toss — iterating on one idea beats taking the
best of several unrelated ones. Runs in the visuals stage and the standalone ``thumbnail`` command,
gated by THUMBNAIL_DIRECTOR_ENABLED. Best-effort: any failure returns ``None`` and the caller falls
back to the built-in template.
"""

from __future__ import annotations

import json
import re

from ..errors import LLMError
from ..logging import get_logger
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model

_MAX_PROMPT_CHARS = 1800
# A refinement round may only make SURGICAL edits. These two guards make that structural rather than
# a request: the model returns find/replace pairs (never a new prompt), each `find` is capped so it
# cannot swallow the draft, and the result is rejected outright if it shrank materially. Asking for a
# whole prompt back is what let an early version throw away a rich cinematic scene and return a plain
# whiteboard with two words on it -- strictly worse than the draft it "fixed".
_MAX_EDIT_FIND_WORDS = 15
_MIN_KEPT_FRACTION = 0.75


class ThumbnailDirector:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="thumbnail_director")

    def compose(
        self,
        concept: str = "",
        *,
        title: str = "",
        niche: str = "",
        description: str = "",
        claim: str = "",
        headline: str = "",
        no_person: bool = False,
    ) -> str | None:
        """Return one image-generation prompt for this video's thumbnail, written by the LLM from the
        video's DESCRIPTION (falling back to the concept, then the title, only when there is no
        description). No guardrails, no composition directions, no presenter/avatar details -- the same
        minimal 'write a thumbnail prompt for this description' request that gives the best results.

        ``claim`` is the video's own spoken hook, and it is passed for exactly one reason: the
        description is SEO copy and rarely carries the video's NUMBERS, so the model used to invent
        plausible ones. A thumbnail promising "5%" over a script that says "10%" contradicts the first
        line the viewer hears.

        ``headline`` is the writer's own ``thumbnail_text`` -- the punchy line stating what the video
        ANSWERS. Without it the model writes its own, and it reliably picks the most striking internal
        statistic instead of the topic, so a video called "Will AI Replace ML Engineers?" shipped a
        thumbnail whose biggest words were "90% ARCHITECTURE" and never named the question at all.

        ``None`` when disabled, empty, or the output is unusable. The extra
        ``concept``/``title``/``niche``/``no_person`` params are unused but kept so existing callers
        need no change."""
        if not self._settings.thumbnail_director_enabled:
            return None
        context = (description or concept or title or "").strip()
        if not context:
            return None
        try:
            draft = self._direct(
                context, claim=(claim or "").strip(), headline=(headline or "").strip()
            )
        except (LLMError, ValueError, AttributeError, TypeError) as exc:
            self._log.warning("thumbnail_director_failed", error=str(exc))
            return None
        return self._polish(context, draft) if draft else draft

    def _polish(self, description: str, draft: str) -> str:
        """Apply up to ``thumbnail_refine_turns`` rounds of SURGICAL edits to the draft.

        The first draft is what ships unless a round finds a concrete defect worth repairing, so this
        can only ever be a touch-up pass. Each round is ONE cheap LIGHT-tier call that both names the
        defects and returns the find/replace pairs that repair them; ``verdict=keep`` exits early, so
        a clean draft costs exactly one extra call.

        Every failure mode keeps the BEST PROMPT SO FAR: an unparseable reply, no applicable edits, a
        rewrite-in-disguise, or a raised error all just end the loop. Refinement can therefore only
        improve the result or leave it untouched -- it can never lose a usable draft.
        """
        best = draft
        for turn in range(1, max(self._settings.thumbnail_refine_turns, 0) + 1):
            try:
                verdict, edits = self._refine(description, best)
            except (LLMError, ValueError, AttributeError, TypeError) as exc:
                self._log.warning("thumbnail_refine_failed", turn=turn, error=str(exc))
                break
            if verdict == "keep" or not edits:
                self._log.info("thumbnail_refine_settled", turn=turn, chars=len(best))
                break
            patched, applied, skipped = _apply_edits(best, edits)
            if not applied:
                self._log.info("thumbnail_refine_no_edits_applied", turn=turn, skipped=skipped)
                break
            # A rewrite cannot arrive as a new prompt any more, so the only way one can sneak in is a
            # handful of edits that between them gut the draft. Length is the cheap tell.
            if len(patched) < int(len(best) * _MIN_KEPT_FRACTION):
                self._log.warning(
                    "thumbnail_refine_rejected_rewrite",
                    turn=turn,
                    before=len(best),
                    after=len(patched),
                )
                break
            best = patched
            self._log.info(
                "thumbnail_refined", turn=turn, chars=len(best), applied=applied, skipped=skipped
            )
        return best

    def _refine(self, description: str, draft: str) -> tuple[str, list[dict]]:
        """One touch-up round. Returns ``(verdict, edits)`` where each edit is a find/replace pair.

        A reply that is not the expected JSON yields ``("keep", [])`` rather than raising, so an
        off-contract model simply ends the loop with the draft intact.
        """
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        resp = self._llm.complete(
            "Repair any defects in this thumbnail prompt, or reply keep if there are none.",
            system=render_prompt(
                load_prompt("thumbnail_refine.system"), description=description, draft=draft
            ),
            temperature=0.2,  # a proofreading pass: the creative swing belongs in the draft
            max_tokens=self._settings.llm_max_tokens,
            model=model,
        )
        try:
            data = json.loads(extract_json(resp.text))
        except (ValueError, TypeError):
            return "keep", []
        if not isinstance(data, dict):
            return "keep", []
        verdict = str(data.get("verdict") or "").strip().lower()
        raw = data.get("edits") or []
        edits = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
        return verdict, edits

    def _direct(self, description: str, *, claim: str = "", headline: str = "") -> str | None:
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        # The claim and the headline go in as SEPARATE labelled blocks rather than being folded into
        # the description, so the model can tell which words are the video's actual argument (and
        # which figures are real) from which are search copy.
        ask = (
            "Write a prompt to generate a thumbnail for a youtube video whose description is given "
            f"below:\n\n{description}"
        )
        if claim:
            ask += f"\n\nTHE VIDEO'S OWN OPENING CLAIM (the only source of figures):\n{claim}"
        if headline:
            ask += (
                "\n\nTHE CREATOR'S OWN THUMBNAIL LINE (what this video is about, in their words). "
                "The PICTURE should carry this idea. If -- and only if -- the frame genuinely needs "
                "words, use this line rather than inventing your own; a strong scene needs "
                f"none:\n{headline}"
            )
        resp = self._llm.complete(
            ask,
            system=load_prompt("thumbnail_director.system"),
            temperature=0.9,
            max_tokens=self._settings.llm_max_tokens,
            model=model,
        )
        prompt = _sanitize(resp.text)
        if prompt:
            self._log.info(
                "thumbnail_directed",
                chars=len(prompt),
                had_claim=bool(claim),
                had_headline=bool(headline),
            )
        return prompt


def _apply_edits(text: str, edits: list[dict]) -> tuple[str, int, int]:
    """Apply find/replace edits to ``text``. Returns ``(result, applied, skipped)``.

    An edit is SKIPPED rather than fatal when its ``find`` is blank, longer than
    ``_MAX_EDIT_FIND_WORDS`` words, or simply absent from the text (the model paraphrased instead of
    quoting). The length check is the one that keeps this a touch-up: without it a single edit could
    name the whole draft as its ``find`` and substitute a completely different prompt -- exactly the
    rewrite this design exists to prevent.

    Only the FIRST occurrence of each ``find`` is replaced, so a common word appearing again later in
    the prompt is never collaterally altered.
    """
    applied = skipped = 0
    for edit in edits:
        find = str(edit.get("find") or "")
        if not find.strip() or len(find.split()) > _MAX_EDIT_FIND_WORDS or find not in text:
            skipped += 1
            continue
        text = text.replace(find, str(edit.get("replace") or ""), 1)
        applied += 1
    return " ".join(text.split()), applied, skipped


# Career-rank adjectives are read by image models as AGE, not seniority: "a senior AI architect"
# comes back grey-haired and in their sixties. The prompt forbids them, but a steer the model can
# quietly ignore is not enough for something this visible -- and a SAVED prompt written before the
# rule existed is reused verbatim, so the rule never even runs. This strips them in code.
_RANK_WORDS = "senior|junior|veteran|principal|seasoned|experienced|entry[- ]level|mid[- ]level"
_ROLE_WORDS = (
    "engineer|architect|scientist|developer|programmer|manager|analyst|designer|researcher|"
    "specialist|technician|consultant|executive|professional|coder|practitioner"
)
_RANKED_PERSON_RE = re.compile(
    rf"\b({_RANK_WORDS})\s+((?:[a-z]+\s+){{0,2}}(?:{_ROLE_WORDS})s?)\b", re.IGNORECASE
)


def strip_career_rank(text: str) -> str:
    """Remove career-rank adjectives that describe a PERSON, leaving the role intact.

    "a focused senior AI architect" -> "a focused AI architect". Image models treat these words as
    age, so they silently add decades to the subject.

    Text INSIDE quotes is left completely alone: a thumbnail legitimately letters "Senior Signal" as
    a column heading or "10% AI CODE" on a box, and rewriting drawn labels would corrupt the very
    thing the frame is trying to say. Quoted spans are masked out before the substitution and
    restored afterwards, so the rule can only ever touch prose describing a human.
    """
    if not text:
        return text
    quoted: list[str] = []

    def _hide(match: re.Match[str]) -> str:
        quoted.append(match.group(0))
        return f"\x00{len(quoted) - 1}\x00"

    masked = re.sub(r"([\"'\u2018\u201c])(?:(?!\1).){0,80}?\1", _hide, text)
    masked = _RANKED_PERSON_RE.sub(lambda m: m.group(2), masked)
    return re.sub(r"\x00(\d+)\x00", lambda m: quoted[int(m.group(1))], masked)


def _sanitize(text: str) -> str | None:
    """Flatten the model's reply into one clean prompt line: drop code fences, a leading 'prompt:'
    label, and wrapping quotes; collapse whitespace; cap the length. ``None`` when nothing usable
    remains."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    t = re.sub(r"^(image\s+)?prompt\s*:\s*", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) >= 2 and t[0] in "\"'" and t[-1] == t[0]:
        t = t[1:-1].strip()
    t = strip_career_rank(t)
    return t[:_MAX_PROMPT_CHARS].strip() or None
