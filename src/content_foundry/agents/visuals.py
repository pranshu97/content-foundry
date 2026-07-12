"""Agent 5 — Visuals & Thumbnail. Deterministic prompts + captions + thumbnail (Ch. 11)."""

from __future__ import annotations

import random
import re
from io import BytesIO
from pathlib import Path

from ..logging import get_logger
from ..models import Provenance, SceneVisual, Script, VisualPackage, VisualShot, VoiceoverAsset
from ..production.captions import write_srt

_THUMB_REL = "assets/thumbnail.png"
_CAPTIONS_REL = "assets/captions.srt"
_MIN_SHOT_SEC = 2.0  # each B-roll beat runs at least this long, to avoid choppiness
# More, shorter beats per scene: slicing a long scene into ~6 clips (not 3 long ones) stops any single
# clip lingering or looping on screen — the fix for visible back-to-back B-roll repetition.
_MAX_SHOTS_PER_SCENE = 6


def build_image_prompt(
    b_roll_keywords: list[str], on_screen_text: str | None, visual_style: str
) -> str:
    """Deterministic per-scene image prompt (Ch. 11.5) — a pure function of its inputs (no LLM)."""
    keywords = ", ".join(b_roll_keywords)
    return (
        f"{visual_style}; {keywords}; on-screen text '{on_screen_text or ''}'; "
        "no logos, no real people"
    )


def _thumbnail_prompt(concept: str) -> str:
    """Turn the script's thumbnail concept into a punchy, high-CTR YouTube-thumbnail image prompt for a
    text-to-image model: one bold subject, exaggerated emotion, dramatic lighting, saturated
    contrasting colors, clean space for the overlaid title, and NO baked-in text (we add the title)."""
    concept = (concept or "a shocked person reacting to a glowing screen").strip().rstrip(".")
    return (
        f"Ultra eye-catching YouTube thumbnail, 16:9, cinematic. Bold central focus: {concept}. "
        "Exaggerated emotion and energy, dramatic rim lighting, punchy saturated contrasting colors, "
        "high contrast, crisp depth of field, glossy professional photo-real render, subtle vignette. "
        "Keep one side as a clean simple background for a big title overlay. "
        "No text, no words, no letters, no watermark, no logos, no real or famous people."
    )


def _broll_source(url: str) -> str:
    """Label a clip by the stock library its URL came from (metadata only)."""
    u = (url or "").lower()
    if "pixabay" in u:
        return "pixabay"
    if "pexels" in u:
        return "pexels"
    if "coverr" in u:
        return "coverr"
    return "stock"


# Stock-video engines match short keyword queries far better than long sentences, so trim each beat
# to its salient words before searching (the full description is kept on the shot for provenance).
_QUERY_STOPWORDS = frozenset({
    # articles / conjunctions / prepositions
    "a", "an", "the", "of", "and", "or", "with", "at", "in", "on", "to", "for", "as", "by",
    "across", "over", "into", "from", "about", "after", "before", "while", "because", "if",
    "but", "yet", "than", "then",
    # pronouns / determiners
    "this", "that", "these", "those", "their", "his", "her", "its", "your", "yours", "our",
    "my", "mine", "we", "us", "you", "they", "them", "it", "he", "she", "him", "who", "whose",
    "which",
    # quantifiers / numbers
    "two", "three", "four", "five", "some", "many", "much", "more", "most", "each", "every",
    "any", "all", "both", "few", "one",
    # be / auxiliary / modal
    "is", "are", "am", "was", "were", "be", "being", "been", "do", "does", "did", "has",
    "have", "had", "can", "will", "would", "should", "could", "may", "might", "must",
    # question words / filler adverbs
    "what", "when", "where", "why", "how", "so", "just", "very", "really", "too", "also",
    "not", "no", "there", "here",
})


def _search_terms(beat: str, *, min_words: int = 2, max_words: int = 4) -> str:
    """Reduce a beat description to a short, PRECISE, stock-searchable query. Drop articles, pronouns,
    auxiliaries, and filler so only the concrete subject/action words remain, then cap at
    ``max_words`` (over-long queries return nothing). If stripping leaves too few words, keep a SHORT
    raw beat whole for context, but for a LONG beat use the content words (never a run of leading
    filler like "how to get a")."""
    raw = [w for w in re.split(r"[^a-z0-9]+", (beat or "").lower()) if w]
    kept = [w for w in raw if w not in _QUERY_STOPWORDS]
    if len(kept) >= min_words:
        words = kept
    elif len(raw) <= max_words:
        words = raw  # short beat -> keep it whole so a lone content word still has context
    else:
        words = kept or raw  # long beat stripped thin -> the content word(s), not leading filler
    return " ".join(words[:max_words]) or (beat or "").strip()


class _BrollPicker:
    """Chooses B-roll clips for one run: keeps the most relevant candidates near the top, adds
    cross-video variety with a per-run seed, and by default uses every clip AT MOST ONCE per video
    (``max_uses=1``) so no shot is ever repeated — once a clip is taken it stays out of the pool and
    ``pick`` returns None, letting the caller reach for a different one."""

    _TOP_K = 8  # sample among the most-relevant eligible clips (a wider window = much more variety)

    def __init__(self, rng: random.Random, *, max_uses: int = 1) -> None:
        self._rng = rng
        self._used: dict[str, int] = {}
        self._prev = ""
        self._max = max_uses

    def pick(self, candidates: list[str]) -> str | None:
        pool = [u for u in dict.fromkeys(candidates) if u]  # de-dup, keep relevance order
        if not pool:
            return None
        # Two tiers: prefer a never-used clip; otherwise (only when a caller raises max_uses above 1)
        # an under-cap clip that ISN'T the one we just showed, so any repeat is never back-to-back. At
        # the default cap of 1 the second tier is always empty, so a clip is NEVER reused anywhere.
        tiers = (
            lambda u: self._used.get(u, 0) == 0,  # fresh (never == prev, since prev was used)
            lambda u: self._used.get(u, 0) < self._max and u != self._prev,  # under cap, not back-to-back
        )
        for eligible in tiers:
            tier = [u for u in pool if eligible(u)]
            if tier:
                window = tier[: self._TOP_K]
                # Bias hard toward the single most relevant clip (search rank 1), but keep a little
                # seeded variety so different runs still differ — weights fall off with the SQUARE
                # of rank, so rank 1 is picked far more often than rank 3-4.
                weights = [r * r for r in range(len(window), 0, -1)]
                chosen = self._rng.choices(window, weights=weights, k=1)[0]
                self._used[chosen] = self._used.get(chosen, 0) + 1
                self._prev = chosen
                return chosen
        return None


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
        # Per-run picker: seeded so different runs pick different clips (varied videos), while
        # de-duping, capping reuse, and never repeating a clip in consecutive scenes.
        picker = _BrollPicker(random.Random(run_id))
        for scene in sorted(script.scenes, key=lambda s: s.index):
            scene_visuals.append(
                self._build_scene_visual(
                    scene, run_root, duration=durations.get(scene.index, 3.0), picker=picker
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
    def _broll_candidates(self, keywords: list[str]) -> list[str]:
        """Pull a pool per keyword (a few searches, one per 'context') and combine — so each scene
        gets a richer, more on-topic set than a single blended query would; downstream de-dup keeps
        the mix varied."""
        combined: list[str] = []
        for kw in keywords[:3]:
            term = (kw or "").strip()
            if not term:
                continue
            try:
                combined.extend(self._broll.search(_search_terms(term)))
            except Exception as exc:  # a flaky search must not kill the scene
                self._log.warning("broll_search_failed", query=term, error=str(exc))
        return combined

    def _build_shots(
        self, scene, run_root: Path, *, duration: float, picker: _BrollPicker
    ) -> list[VisualShot]:
        """Break the scene into ordered visual beats — one B-roll clip per keyword/description, each
        matched to that moment — so the footage changes with the narration instead of one broad clip
        covering the whole scene."""
        beats = [k.strip() for k in scene.b_roll_keywords if k and k.strip()]
        n = max(1, min(len(beats), int(duration // _MIN_SHOT_SEC) or 1, _MAX_SHOTS_PER_SCENE))
        found: list[tuple[str, str, str]] = []  # (rel_path, source, query)
        for j, beat in enumerate(beats[:n]):
            # Footage for THIS beat; if every candidate for the beat is already used elsewhere, widen
            # to the scene's other queries so a hole is filled with a DIFFERENT fresh clip instead of
            # being left empty or repeated (the picker still never reuses a clip).
            url = picker.pick(self._broll_candidates([beat])) or picker.pick(
                self._broll_candidates(beats)
            )
            if not url:
                continue
            rel = f"assets/scenes/scene_{scene.index}_shot_{j}.mp4"
            (run_root / rel).write_bytes(self._broll.download(url))
            found.append((rel, _broll_source(url), beat))
        if not found:
            return []
        per = round(duration / len(found), 3)  # split the scene evenly across the beats we found
        return [VisualShot(path=r, duration_sec=per, source=src, query=q) for r, src, q in found]

    def _build_scene_visual(
        self, scene, run_root: Path, *, duration: float, picker: _BrollPicker
    ) -> SceneVisual:
        broll_enabled = bool(self._broll and getattr(self._broll, "enabled", False))
        if scene.b_roll_keywords and broll_enabled:
            shots = self._build_shots(scene, run_root, duration=duration, picker=picker)
            if shots:
                return SceneVisual(
                    scene_index=scene.index,
                    kind="broll",
                    path=shots[0].path,
                    source=shots[0].source,
                    prompt_or_query=", ".join(scene.b_roll_keywords[:_MAX_SHOTS_PER_SCENE]),
                    on_screen_text=scene.on_screen_text,
                    sfx=scene.sfx,
                    duration_sec=round(duration, 3),
                    shots=shots,
                )

        prompt = build_image_prompt(
            scene.b_roll_keywords, scene.on_screen_text, self._settings.visual_style
        )
        rel = f"assets/scenes/scene_{scene.index}.png"
        target = run_root / rel
        if self._image is not None:
            # Scene backgrounds fill the video frame, so generate at the VIDEO resolution (not the
            # smaller thumbnail size) — otherwise every scene image is upscaled from 720p and looks
            # soft. The card fallback below already renders at the full video resolution.
            data = self._image.generate(prompt, size=self._settings.video_resolution)
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
            sfx=scene.sfx,
            duration_sec=round(duration, 3),
        )

    # -------------------------------------------------------------- thumbnail
    def _compose_thumbnail(self, concept: str, text: str, target: Path) -> None:
        size = self._settings.thumbnail_wh
        base: bytes | None = None
        if self._image is not None:
            try:
                base = self._image.generate(
                    _thumbnail_prompt(concept), size=self._settings.thumbnail_size
                )
            except Exception as exc:  # a thumbnail image failure must never crash the run
                self._log.warning("thumbnail_image_failed", error=str(exc))
        _write_card(text, size, target, base_png=base, punchy=True)


# --------------------------------------------------------------------- Pillow
def _write_card(
    text: str, size_wh: tuple[int, int], target: Path, base_png: bytes | None = None,
    *, punchy: bool = False,
):
    """Render a title card. ``punchy`` = a high-CTR YouTube-thumbnail overlay (bold UPPERCASE, a dark
    bottom scrim, a drop shadow + heavy outline, and numbers/the key word highlighted); otherwise a
    clean caption card (accent bar + centered shadowed text) used for scene fallbacks."""
    from PIL import Image, ImageDraw

    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = size_wh
    if base_png:
        img = Image.open(BytesIO(base_png)).convert("RGB").resize(size_wh)
        darken = 0.12 if punchy else 0.28  # keep a punchy thumb vivid; its scrim handles legibility
        img = Image.blend(img, Image.new("RGB", size_wh, (8, 11, 20)), darken)
    else:
        img = _gradient_bg(size_wh)

    if punchy:
        _draw_punchy_title(img, text or "", size_wh)
        img.save(target, format="PNG")
        return

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

    stroke = max(3, int(height * 0.008))  # heavy black outline so the title pops on a vivid image
    draw.rectangle([margin, y0, margin + bar_w, y0 + block_h], fill=(56, 189, 248))  # accent bar
    y = y0
    for line in lines:
        draw.text(
            (x_text, y), line, font=font, fill=(255, 255, 255),
            stroke_width=stroke, stroke_fill=(0, 0, 0),
        )
        y += line_h
    img.save(target, format="PNG")


_THUMB_ACCENT = (253, 224, 71)  # bright yellow — the number / power-word highlight


def _draw_punchy_title(img, text: str, size_wh: tuple[int, int]) -> None:
    """High-CTR thumbnail text: big bold UPPERCASE, bottom-anchored over a dark scrim, a drop shadow
    + heavy outline, and the numbers (or the single strongest word) in a punchy accent color."""
    from PIL import ImageDraw

    width, height = size_wh
    draw = ImageDraw.Draw(img, "RGBA")
    title = " ".join((text or "").upper().split()) or "WATCH THIS"
    margin = int(width * 0.06)
    box_w = width - 2 * margin

    # Auto-fit: shrink the bold face until the title fits in <= 3 lines within the box and ~60% height.
    size = int(height * 0.11)
    floor = int(height * 0.05)
    font = _load_display_font(size)
    lines = _wrap_to_width(draw, title, font, box_w, max_lines=4)
    while size > floor:
        font = _load_display_font(size)
        lines = _wrap_to_width(draw, title, font, box_w, max_lines=4)
        lh = _line_height(draw, font)
        if (len(lines) <= 3 and lh * len(lines) <= int(height * 0.6)
                and all(draw.textlength(ln, font=font) <= box_w for ln in lines)):
            break
        size -= 4
    lines = _wrap_to_width(draw, title, font, box_w, max_lines=3)
    lh = _line_height(draw, font)
    block_h = lh * len(lines)
    y = height - int(height * 0.07) - block_h  # sit the block near the bottom

    # Dark bottom scrim so the text reads over any image.
    scrim_top = max(0, y - int(height * 0.05))
    for yy in range(scrim_top, height):
        a = int(215 * (yy - scrim_top) / max(1, height - scrim_top))
        draw.line([(0, yy), (width, yy)], fill=(0, 0, 0, a))

    # Highlight words with a digit; if none, the single longest word (usually the key noun).
    tokens = title.split()
    has_num = any(any(c.isdigit() for c in w) for w in tokens)
    longest = max(tokens, key=len) if tokens else ""

    def _hot(word: str) -> bool:
        return any(c.isdigit() for c in word) if has_num else word == longest

    stroke = max(4, int(size * 0.10))
    shadow = max(3, int(size * 0.06))
    for line in lines:
        x = margin
        for word in line.split(" "):
            token = word + " "
            color = _THUMB_ACCENT if _hot(word) else (255, 255, 255)
            draw.text((x + shadow, y + shadow), token, font=font, fill=(0, 0, 0, 190))  # drop shadow
            draw.text(
                (x, y), token, font=font, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0)
            )
            x += draw.textlength(token, font=font)
        y += lh


def _load_display_font(size: int):
    """A heavy display face for thumbnails (Impact / Arial Black / a bold fallback)."""
    from PIL import ImageFont

    for path in (
        "C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/ariblk.ttf",
        "C:/Windows/Fonts/arialbd.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return _load_font(size)


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
