"""Agent 5 — Visuals & Thumbnail. Deterministic prompts + captions + thumbnail (Ch. 11)."""

from __future__ import annotations

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
        used_clips: dict[str, int] = {}
        for scene in sorted(script.scenes, key=lambda s: s.index):
            scene_visuals.append(
                self._build_scene_visual(
                    scene, run_root, duration=durations.get(scene.index, 3.0),
                    used_clips=used_clips,
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
    def _pick_broll(self, query: str, used: dict[str, int], *, max_uses: int = 2) -> str | None:
        """Pick a clip, preferring one not yet used and never reusing any clip more than ``max_uses``
        times across the video, so B-roll doesn't visibly repeat."""
        candidates = self._broll.search(query)
        for threshold in (0, max_uses - 1):
            for url in candidates:
                if used.get(url, 0) <= threshold:
                    used[url] = used.get(url, 0) + 1
                    return url
        return None

    def _build_scene_visual(
        self, scene, run_root: Path, *, duration: float, used_clips: dict[str, int]
    ) -> SceneVisual:
        broll_enabled = bool(self._broll and getattr(self._broll, "enabled", False))
        if scene.b_roll_keywords and broll_enabled:
            query = " ".join(scene.b_roll_keywords[:2])
            url = self._pick_broll(query, used_clips)
            if url:
                rel = f"assets/scenes/scene_{scene.index}.mp4"
                (run_root / rel).write_bytes(self._broll.download(url))
                return SceneVisual(
                    scene_index=scene.index,
                    kind="broll",
                    path=rel,
                    source="pexels",
                    prompt_or_query=query,
                    on_screen_text=scene.on_screen_text,
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
            on_screen_text=scene.on_screen_text,
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
    """Render a clean title card: gradient (or darkened image) + accent bar + big shadowed text."""
    from PIL import Image, ImageDraw

    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = size_wh
    if base_png:
        img = Image.open(BytesIO(base_png)).convert("RGB").resize(size_wh)
        img = Image.blend(img, Image.new("RGB", size_wh, (8, 11, 20)), 0.5)  # darken for legibility
    else:
        img = _gradient_bg(size_wh)

    draw = ImageDraw.Draw(img)
    margin = int(width * 0.08)
    bar_w = max(6, width // 200)
    gap = int(width * 0.025)
    font = _load_font(int(height * 0.09))
    lines = _wrap_to_width(draw, text or "", font, width - 2 * margin - bar_w - gap)

    line_h = _line_height(draw, font)
    block_h = line_h * len(lines)
    y0 = max(margin, (height - block_h) // 2)
    x_text = margin + bar_w + gap

    draw.rectangle([margin, y0, margin + bar_w, y0 + block_h], fill=(56, 189, 248))  # accent bar
    y = y0
    for line in lines:
        draw.text((x_text + 3, y + 3), line, font=font, fill=(0, 0, 0))  # shadow
        draw.text((x_text, y), line, font=font, fill=(244, 246, 252))  # text
        y += line_h
    img.save(target, format="PNG")


def _gradient_bg(size_wh: tuple[int, int]):
    from PIL import Image, ImageDraw

    width, height = size_wh
    top, bottom = (30, 41, 59), (2, 6, 23)  # slate-800 -> slate-950
    img = Image.new("RGB", size_wh)
    draw = ImageDraw.Draw(img)
    span = max(1, height - 1)
    for y in range(height):
        t = y / span
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )
    return img


def _load_font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)  # scalable DejaVu (Pillow >= 10.1)
    except Exception:  # pragma: no cover - ancient Pillow / no freetype
        return ImageFont.load_default()


def _line_height(draw, font) -> int:
    box = draw.textbbox((0, 0), "Ag", font=font)
    return int((box[3] - box[1]) * 1.45)


def _wrap_to_width(draw, text: str, font, max_w: int, *, max_lines: int = 6) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_w or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:max_lines]
