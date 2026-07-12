"""Agent 5.5 — B-roll Director. Leverages the LLM's whole-script context (e.g. Gemini) to rewrite each
scene's B-roll search queries so the stock footage is both RELEVANT to what the narrator says AND
visually DIVERSE across the whole video (no repeated shots). Runs in the visuals stage, gated by
BROLL_DIRECTOR_ENABLED. Best-effort: any failure keeps the script generator's original keywords.
"""

from __future__ import annotations

import json

from ..errors import LLMError
from ..logging import get_logger
from ..models import Script
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model


class BrollDirector:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="broll_director")

    def run(self, script: Script) -> Script:
        """Rewrite each scene's ``b_roll_keywords`` in place with relevant, diverse queries. A no-op
        when disabled, the script has no scenes, or the LLM output is unusable."""
        if not self._settings.broll_director_enabled or not script.scenes:
            return script
        try:
            queries = self._direct(script)
        except (json.JSONDecodeError, LLMError, ValueError, AttributeError, TypeError) as exc:
            self._log.warning("broll_director_failed", error=str(exc))
            return script
        applied = 0
        for scene in script.scenes:
            directed = queries.get(scene.index)
            if directed:
                scene.b_roll_keywords = directed
                applied += 1
        if applied:
            self._log.info("broll_directed", scenes=applied)
        return script

    def _direct(self, script: Script) -> dict[int, list[str]]:
        # Hand the model the WHOLE script (every scene's narration) so it can keep the shots relevant
        # per scene AND deliberately diverse across scenes.
        scenes_json = json.dumps(
            [{"index": s.index, "narration": s.narration} for s in script.scenes],
            ensure_ascii=False,
        )
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        system = render_prompt(
            load_prompt("broll_director.system"),
            max_queries=str(self._settings.broll_director_max_queries),
            scenes_json=scenes_json,
        )
        resp = self._llm.complete(
            "Return ONLY the JSON now.",
            system=system,
            temperature=0.4,
            max_tokens=self._settings.llm_max_tokens,
            model=model,
        )
        # The model may return a bare array or a {"scenes": [...]} object; extract_json only recovers
        # objects, so try a direct parse first, then fall back to it.
        try:
            data = json.loads(resp.text.strip())
        except json.JSONDecodeError:
            data = json.loads(extract_json(resp.text))
        items = data.get("scenes") if isinstance(data, dict) else data
        out: dict[int, list[str]] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            queries = [
                q.strip() for q in (item.get("queries") or []) if isinstance(q, str) and q.strip()
            ]
            if idx is not None and queries:
                out[int(idx)] = queries[: self._settings.broll_director_max_queries]
        return out
