"""Agent 5.6 — Thumbnail Director. Uses the LLM's per-video context to write a rich, creative
IMAGE-GENERATION prompt for the thumbnail from the script's concept/title/niche, instead of the
generic static template — so the thumbnail is a bespoke, high-CTR scene rather than generic AI slop,
with a hard NO-TEXT instruction so the image model stops baking in garbled "hieroglyph" lettering.
Runs in the visuals stage and the standalone ``thumbnail`` command, gated by THUMBNAIL_DIRECTOR_ENABLED.
Best-effort: any failure returns ``None`` and the caller falls back to the built-in template.
"""

from __future__ import annotations

import re

from ..errors import LLMError
from ..logging import get_logger
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider
from ..providers.tiering import TaskTier, select_model

_MAX_PROMPT_CHARS = 900


class ThumbnailDirector:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="thumbnail_director")

    def compose(
        self, concept: str, *, title: str = "", niche: str = "",
        thumbnail_text: str = "", no_person: bool = False,
    ) -> str | None:
        """Return one vivid, ready-to-use image-generation prompt for this video's thumbnail, or
        ``None`` when disabled or the LLM output is unusable (the caller then falls back to the
        built-in template). ``no_person`` asks for a people-free background because the operator's
        face is composited in separately."""
        if not self._settings.thumbnail_director_enabled:
            return None
        concept = (concept or "").strip()
        title = (title or "").strip()
        if not concept and not title:
            return None
        try:
            return self._direct(
                concept, title=title, niche=niche, thumbnail_text=thumbnail_text, no_person=no_person
            )
        except (LLMError, ValueError, AttributeError, TypeError) as exc:
            self._log.warning("thumbnail_director_failed", error=str(exc))
            return None

    def _direct(self, concept, *, title, niche, thumbnail_text, no_person) -> str | None:
        person_clause = (
            "COMPOSITION: NO people and NO faces anywhere (a presenter is composited in separately). "
            "Build the topic-relevant SETTING itself — the office, cubicles, desks, monitors, "
            "whiteboard, campus, etc. the concept implies — as a bold scene with real depth, and keep "
            "the RIGHT side more open for a person plus a calmer area for a large title overlay."
            if no_person else
            "COMPOSITION: ONE main person in a topic-relevant setting as the clear focal subject, "
            "facing the camera with an instantly-readable, exaggerated emotion; keep their face the "
            "largest, sharpest, unobstructed face in the frame (a close-up, waist-up, standing, or a "
            "medium shot all work). Leave a calmer area on one side or along the top or bottom for a "
            "large title overlay."
        )
        appearance = (getattr(self._settings, "avatar_appearance", "") or "").strip()
        if appearance and not no_person:
            person_clause += (
                f" MATCH THE PRESENTER: the person's face is swapped in afterwards, so make the "
                f"generated person LOOK like them: {appearance}. Keep the same age, gender, skin "
                "tone, hair, and facial hair — a close match is what makes the swap convincing."
            )
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        system = render_prompt(
            load_prompt("thumbnail_director.system"),
            niche=niche or "",
            title=title or concept,
            concept=concept or title,
            overlay_text=thumbnail_text or "",
            person_clause=person_clause,
        )
        resp = self._llm.complete(
            "Write the single image prompt now. Output ONLY the prompt text.",
            system=system,
            temperature=0.85,
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
