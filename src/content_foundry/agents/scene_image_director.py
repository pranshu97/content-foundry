"""Agent 5.7 — Scene Image Director. When a shot gets NO relevant stock B-roll, an LLM art-director
writes a vivid, witty, richly descriptive text-to-image prompt for that beat (grounded in the scene's
narration) so the gap is filled with a bespoke, ON-TOPIC image instead of a borrowed off-topic clip.
Runs inside the visuals stage, gated by SCENE_IMAGE_DIRECTOR_ENABLED. Best-effort: any failure lets
the caller fall back to the deterministic image-prompt template.
"""

from __future__ import annotations

import json

from ..logging import get_logger
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model


class SceneImageDirector:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="scene_image_director")

    def compose(
        self, *, beats: list[str], narration: str = "", on_screen_text: str = "", niche: str = ""
    ) -> dict[str, str]:
        """Return ``{beat: image_prompt}`` for the given gap beats — one LLM call for the whole scene.
        Empty when there are no beats or the model output is unusable (the caller then uses its
        deterministic template). Beats the model omits or renames simply fall back per-beat."""
        wanted = [b.strip() for b in beats if b and b.strip()]
        if not wanted:
            return {}
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        system = render_prompt(
            load_prompt("scene_image_director.system"),
            beats_json=json.dumps(wanted, ensure_ascii=False),
            narration=narration or "",
            on_screen=on_screen_text or "",
            style=self._settings.visual_style,
            niche=niche or self._settings.target_niche or "",
        )
        resp = self._llm.complete(
            "Return ONLY the JSON now.",
            system=system,
            temperature=0.7,
            max_tokens=self._settings.llm_max_tokens,
            model=model,
        )
        # The model may return a bare array or a {"shots": [...]} object; extract_json only recovers
        # objects, so try a direct parse first, then fall back to it.
        try:
            data = json.loads(resp.text.strip())
        except json.JSONDecodeError:
            data = json.loads(extract_json(resp.text))
        items = data.get("shots") if isinstance(data, dict) else data
        # Map the model's echoed beat back to the EXACT input string (case-insensitive) so the caller's
        # `prompts.get(beat)` lookup hits even if the model altered the casing.
        by_norm = {b.lower(): b for b in wanted}
        out: dict[str, str] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            key = by_norm.get((item.get("beat") or "").strip().lower())
            prompt = (item.get("prompt") or "").strip()
            if key and prompt:
                out[key] = prompt
        return out
