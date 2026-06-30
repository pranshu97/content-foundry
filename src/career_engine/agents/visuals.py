"""Agent 5 — Visuals & Thumbnail. Deterministic prompts + captions + thumbnail (Ch. 11)."""

from __future__ import annotations

import textwrap
from io import BytesIO
from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, SceneVisual, Script, VisualPackage, VoiceoverAsset
from ..production.captions import write_srt

_THUMB_REL = "assets/thumbnail.png"
_CAPTIONS_REL = "assets/captions.srt"


def build_image_prompt(
    b_roll_keywords: list[str], on_screen_text: str | None, visual_style: str
) -> str:
    """Deterministic per-scene image prompt (Ch. 11.5) — a pure function of its inputs (no LLM)."""
    keywords = ", ".join(b_roll_keywords)
    return (
        f"{visual_style}; {keywords}; on-screen text '{on_screen_text or ''}'; "
        "no logos, no real people"
    )


class Visuals:
    def __init__(self, settings, image_provider=None, broll_client=None):
        self._settings = settings
        self._image = image_provider
        self._broll = broll_client
        self._log = get_logger(component="visuals")

    def run(
        self, run_id: str, script: Script, voiceover: VoiceoverAsset, *, run_root: Path
    ) -> VisualPackage:
        durations = {st.scene_index: (st.end - st.start) for st in voiceover.scene_timings}
        scenes_dir = run_root / "assets" / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        scene_visuals: list[SceneVisual] = []
        for scene in sorted(script.scenes, key=lambda s: s.index):
            scene_visuals.append(
                self._build_scene_visual(
                    scene, run_root, duration=durations.get(scene.index, 3.0)
                )
            )

        # Captions from word timings.
        captions_path = run_root / _CAPTIONS_REL
        write_srt(captions_path, voiceover.word_timings)

        # Thumbnail.
        thumbnail_text = (script.title_options or [script.thumbnail_concept or "Career Advice"])[0]
        self._compose_thumbnail(script.thumbnail_concept, thumbnail_text, run_root / _THUMB_REL)

        return VisualPackage(
            run_id=run_id,
            thumbnail_path=_THUMB_REL,
            thumbnail_text=thumbnail_text,
            captions_path=_CAPTIONS_REL,
            scenes=scene_visuals,
            visual_style=self._settings.visual_style,
            provenance=Provenance(
                produced_by="visuals", model=None, config_hash=self._settings.config_hash
            ),
        )

    # ------------------------------------------------------------------ scene
    def _build_scene_visual(self, scene, run_root: Path, *, duration: float) -> SceneVisual:
        broll_enabled = bool(self._broll and getattr(self._broll, "enabled", False))
        if scene.b_roll_keywords and broll_enabled:
            query = " ".join(scene.b_roll_keywords[:2])
            url = self._broll.search(query)
            if url:
                rel = f"assets/scenes/scene_{scene.index}.mp4"
                (run_root / rel).write_bytes(self._broll.download(url))
                return SceneVisual(
                    scene_index=scene.index,
                    kind="broll",
                    path=rel,
                    source="pexels",
                    prompt_or_query=query,
                    duration_sec=round(duration, 3),
                )

        prompt = build_image_prompt(
            scene.b_roll_keywords, scene.on_screen_text, self._settings.visual_style
        )
        rel = f"assets/scenes/scene_{scene.index}.png"
        target = run_root / rel
        if self._image is not None:
            data = self._image.generate(prompt, size=self._settings.thumbnail_size)
            target.write_bytes(data)
            source = getattr(self._image, "name", self._settings.image_provider)
        else:
            _write_card(scene.on_screen_text or prompt, self._settings.resolution_wh, target)
            source = "card"
        return SceneVisual(
            scene_index=scene.index,
            kind="image",
            path=rel,
            source=source,
            prompt_or_query=prompt,
            duration_sec=round(duration, 3),
        )

    # -------------------------------------------------------------- thumbnail
    def _compose_thumbnail(self, concept: str, text: str, target: Path) -> None:
        size = self._settings.thumbnail_wh
        base: bytes | None = None
        if self._image is not None:
            prompt = (
                f"{self._settings.visual_style}; {concept}; bold thumbnail; "
                "high contrast; no logos, no real people"
            )
            base = self._image.generate(prompt, size=self._settings.thumbnail_size)
        _write_card(text, size, target, base_png=base)


# --------------------------------------------------------------------- Pillow
def _write_card(text: str, size_wh: tuple[int, int], target: Path, base_png: bytes | None = None):
    from PIL import Image, ImageDraw

    target.parent.mkdir(parents=True, exist_ok=True)
    if base_png:
        img = Image.open(BytesIO(base_png)).convert("RGB").resize(size_wh)
    else:
        img = Image.new("RGB", size_wh, color=(17, 24, 39))
    draw = ImageDraw.Draw(img)
    wrapped = textwrap.fill(text or "", width=max(10, size_wh[0] // 28))
    draw.multiline_text(
        (size_wh[0] // 2, size_wh[1] // 2),
        wrapped,
        fill=(245, 245, 245),
        anchor="mm",
        align="center",
    )
    img.save(target, format="PNG")
