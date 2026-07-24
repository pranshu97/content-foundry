"""Agent 5.6 — Thumbnail Director. Asks the LLM for ONE text-to-image prompt from the video's
DESCRIPTION, driven by a single high-quality worked EXAMPLE (a description->prompt pair in
``thumbnail_director.system.txt``) so the output matches that cinematic caliber — no rulebook, no
presenter/avatar details. Runs in the visuals stage and the standalone ``thumbnail`` command, gated by
THUMBNAIL_DIRECTOR_ENABLED. Best-effort: any failure returns ``None`` and the caller falls back to the
built-in template.
"""

from __future__ import annotations

import re

from ..errors import LLMError
from ..logging import get_logger
from ..prompts import load_prompt
from ..providers.base import LLMProvider
from ..providers.tiering import TaskTier, select_model

_MAX_PROMPT_CHARS = 1800


class ThumbnailDirector:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="thumbnail_director")

    def compose(
        self, concept: str = "", *, title: str = "", niche: str = "",
        description: str = "", no_person: bool = False,
    ) -> str | None:
        """Return one image-generation prompt for this video's thumbnail, written by the LLM from the
        video's DESCRIPTION alone (falling back to the concept, then the title, only when there is no
        description). No guardrails, no composition directions, no presenter/avatar details — the same
        minimal 'write a thumbnail prompt for this description' request that gives the best results.
        ``None`` when disabled, empty, or the output is unusable. The extra
        ``concept``/``title``/``niche``/``no_person`` params are unused but kept so existing callers
        need no change."""
        if not self._settings.thumbnail_director_enabled:
            return None
        context = (description or concept or title or "").strip()
        if not context:
            return None
        try:
            return self._direct(context)
        except (LLMError, ValueError, AttributeError, TypeError) as exc:
            self._log.warning("thumbnail_director_failed", error=str(exc))
            return None

    def _direct(self, description: str) -> str | None:
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        resp = self._llm.complete(
            "Write a prompt to generate a thumbnail for a youtube video whose description is given "
            f"below:\n\n{description}",
            system=load_prompt("thumbnail_director.system"),
            temperature=0.9,
            max_tokens=self._settings.llm_max_tokens,
            model=model,
        )
        prompt = _sanitize(resp.text)
        if prompt:
            self._log.info("thumbnail_directed", chars=len(prompt))
        return prompt


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
    return t[:_MAX_PROMPT_CHARS].strip() or None
