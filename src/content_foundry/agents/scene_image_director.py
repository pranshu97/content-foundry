"""Agent 5.7 — Scene Image Director. When a shot gets NO relevant stock B-roll, an LLM art-director
writes a vivid, witty, richly descriptive text-to-image prompt for that beat (grounded in the scene's
narration) so the gap is filled with a bespoke, ON-TOPIC image instead of a borrowed off-topic clip.
Runs inside the visuals stage, gated by SCENE_IMAGE_DIRECTOR_ENABLED. Best-effort: any failure lets
the caller fall back to the deterministic image-prompt template.
"""

from __future__ import annotations

import json

from ..logging import get_logger
from ..production.diagram import diagram_type
from ..prompts import load_prompt, render_prompt
from ..providers.base import LLMProvider, extract_json
from ..providers.tiering import TaskTier, select_model

_MAX_AVOID = 14  # bound the context: the most recent compositions carry the most risk of repetition
_SIGNATURE_WORDS = 14  # enough to capture "An over-the-shoulder shot of a dim minimalist office"


def composition_signature(prompt: str) -> str:
    """The opening clause of a prompt \u2014 where the camera strategy, subject and setting are declared.

    That is exactly what was repeating across scenes (8 of 12 shots in one run opened with
    "over-the-shoulder..."), so it is the part later scenes must be shown in order to avoid it.
    """
    return " ".join((prompt or "").split()[:_SIGNATURE_WORDS]).strip()


class SceneImageDirector:
    def __init__(self, settings, llm_provider: LLMProvider):
        self._settings = settings
        self._llm = llm_provider
        self._log = get_logger(component="scene_image_director")
        # shot index -> diagram spec, for shots the director judged better DRAWN than photographed.
        # Populated by compose(); read by the caller straight after, so the instance must be kept.
        self.diagrams: dict[int, dict] = {}

    def compose(
        self,
        *,
        shots: list[tuple[int, str]],
        narration: str = "",
        on_screen_text: str = "",
        niche: str = "",
        title: str = "",
        description: str = "",
        already_used: list[str] | None = None,
    ) -> dict[int, str]:
        """Return ``{shot_index: image_prompt}`` for the given shots — one LLM call for the whole scene.
        Empty when there are no shots or the model output is unusable (the caller then uses its
        deterministic template). Shots the model omits simply fall back individually.

        ``shots`` pairs each shot's index with the EXACT words spoken while it is on screen. That line
        is the only content signal the director gets: the script's stock-search beat used to be passed
        too, and it actively dragged images toward generic footage of the domain (a scene about pay
        bands carried the beat "corporate interview panel room", so the image showed an interview
        instead of the money). Keying by INDEX rather than by beat text also means two shots that
        happen to share a beat can never collapse onto one image.
        """
        wanted = [(i, line.strip()) for i, line in shots if line and line.strip()]
        if not wanted:
            return {}
        model = select_model(
            self._settings, TaskTier.LIGHT, fallback=self._settings.generator_model
        )
        used = [s for s in (already_used or []) if s.strip()][-_MAX_AVOID:]
        beats_payload = [
            {"shot": i, "spoken_while_this_shot_is_on_screen": line} for i, line in wanted
        ]
        system = render_prompt(
            load_prompt("scene_image_director.system"),
            beats_json=json.dumps(beats_payload, ensure_ascii=False, indent=2),
            narration=narration or "",
            on_screen=on_screen_text or "",
            style=self._settings.visual_style,
            niche=niche or self._settings.target_niche or "",
            # The video's own title/description are what let a generic beat hint ("person working in
            # modern office") become a shot about THIS video — the same lever that lifted the
            # thumbnail director. render_prompt is a named .replace, so these must ALWAYS be passed.
            title=title or "",
            description=description or "",
            # Each scene is its own LLM call with no memory of the others, so without this every
            # scene independently reaches for the same safe composition.
            already_used="\n".join(f"    - {s}" for s in used) or "    (nothing yet)",
        )
        resp = self._llm.complete(
            "Return ONLY the JSON now.",
            system=system,
            temperature=0.85,  # a touch of spread so shots in one scene don't converge
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
        valid = {i for i, _ in wanted}
        out: dict[int, str] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            raw = item.get("shot")
            if raw is None:
                continue
            try:
                key = int(raw)
            except (TypeError, ValueError):
                continue
            prompt = (item.get("prompt") or "").strip()
            if key in valid and prompt:
                out[key] = prompt
                # A shot may ALSO carry a diagram spec. It rides a side channel rather than changing
                # compose()'s return type, so every existing caller is untouched — and the prompt is
                # still required, which guarantees a fallback when the render fails.
                spec = item.get("diagram")
                if diagram_type(spec) and isinstance(spec, dict):
                    self.diagrams[key] = spec
        return out
