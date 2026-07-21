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
_THUMB_PROMPT_REL = "assets/thumbnail_prompt.txt"  # the editable image prompt used for the thumbnail
_MIN_SHOT_SEC = 2.0  # each B-roll beat runs at least this long, to avoid choppiness
# More, shorter beats per scene: slicing a long scene into up to 8 clips (not a few long ones) stops
# any single clip lingering or being slowed to fill the gap — more distinct footage, less stretching.
_MAX_SHOTS_PER_SCENE = 20

# Editor 'cut' hint -> multiplier on the min seconds-per-shot: faster cutting packs MORE, shorter
# shots into a scene; holding uses fewer, longer ones — so the render follows the script's pacing.
_CUT_PACE = {
    "fast": 0.6, "quick": 0.6, "rapid": 0.55, "snappy": 0.6, "hard": 0.7, "energetic": 0.65,
    "hold": 1.8, "slow": 1.6, "linger": 1.9, "static": 2.0, "calm": 1.5,
}


def _cut_pace(cut: str | None) -> float:
    """Map a scene's 'cut' hint to a min-shot-seconds multiplier (fast => more shots, hold => fewer).
    Unknown/empty => 1.0 (neutral)."""
    key = (cut or "").strip().lower()
    for word, pace in _CUT_PACE.items():
        if word in key:
            return pace
    return 1.0


def build_image_prompt(
    b_roll_keywords: list[str], on_screen_text: str | None, visual_style: str
) -> str:
    """Deterministic per-scene image prompt (Ch. 11.5) — a pure function of its inputs (no LLM)."""
    keywords = ", ".join(b_roll_keywords)
    return (
        f"{visual_style}; cinematic editorial photograph of {keywords}; dramatic directional "
        "lighting, shallow depth of field, rich saturated color grade, sharp detail, professional "
        f"composition with clean negative space; subtle on-screen text '{on_screen_text or ''}'; "
        "no logos, no watermark, no real people"
    )


def _cap_words(text: str, max_words: int) -> str:
    """Keep at most ``max_words`` words so the thumbnail overlay stays scannable at a glance."""
    words = (text or "").split()
    return " ".join(words[:max_words]) if len(words) > max_words else (text or "").strip()


_EMOTION_KEYWORDS = (
    "shocked", "stunned", "surprised", "amazed", "excited", "thrilled", "happy", "smiling",
    "angry", "furious", "serious", "confident", "smug", "worried", "anxious", "confused",
    "curious", "disgusted", "crying", "laughing",
)


def _detect_emotion(concept: str) -> str:
    """Pick the emotion the thumbnail concept describes so a matching avatar variant
    (``avatar_<emotion>.png``) can be chosen. Empty when none is named (base avatar is used)."""
    low = (concept or "").lower()
    return next((w for w in _EMOTION_KEYWORDS if w in low), "")


def _fallback_thumb_text(title: str) -> str:
    """A short punchy thumbnail line when the writer supplied none: drop parentheticals + weak
    lead-ins and keep the first few strong words (a whole long title overlaid on a thumbnail is
    unreadable)."""
    text = re.sub(r"\s*\([^)]*\)", "", title or "").strip()
    text = re.sub(r"^(how to|how|why|what|the|a|an)\s+", "", text, flags=re.IGNORECASE).strip()
    words = text.split()
    return " ".join(words[:6]) if words else (title or "").strip()


def _thumbnail_prompt(concept: str, *, no_person: bool = False, appearance: str = "") -> str:
    """Turn the script's thumbnail concept into a punchy, high-CTR YouTube-thumbnail image prompt for a
    text-to-image model: ONE hero person (face visible, facing camera) inside a TOPIC-RELEVANT setting
    with concrete props, a big emotion, bright clean face light, saturated color in the environment,
    room for the overlaid title, and NO baked-in text (we add the title). When ``no_person`` (the
    operator's avatar face is composited in separately) it asks for the relevant, people-free SETTING
    so the avatar is the ONLY face."""
    concept = (concept or "a shocked person reacting to a glowing screen").strip().rstrip(".")
    if no_person:
        return (
            f"A real, professional DSLR PHOTOGRAPH used as a high-CTR YouTube thumbnail BACKGROUND, "
            f"16:9, about: {concept}. NO people, no faces, no person at all (a presenter is composited "
            "in separately). Build the REAL, topic-relevant SETTING itself with concrete props (for a "
            "tech-career video: a modern open-plan tech office, cubicles and desks with laptops and "
            "dual monitors of code, a whiteboard of algorithm diagrams, a campus lobby) as a bold scene "
            "with real depth, bright DAYLIT high-key lighting, and vivid accent colors (NOT a dark "
            "neon-night look) in the environment. "
            "Keep it uncluttered with shallow depth of field; keep the RIGHT side more open for a "
            "person and a calmer area for a large title overlay. Shot on a 35mm lens, real photographic "
            "detail — a REAL photo, NOT a 3D render, NOT CGI, NOT digital art, no plastic or over-"
            "smoothed surfaces. Bright, not dark or muddy. Absolutely no text, letters, numbers, "
            "watermark, logos, UI, or real/famous people."
        )
    look = (
        f" The main person LOOKS like {appearance}: keep that same age, gender, skin tone, hair, "
        "and facial hair (their real face is swapped in afterwards)." if appearance else ""
    )
    return (
        f"A real, professional DSLR PHOTOGRAPH used as a high-CTR YouTube thumbnail, 16:9, about: "
        f"{concept}. Stage it in a REAL, topic-relevant setting with concrete props (for a tech-career "
        "video: a modern open-plan tech office, cubicles with coworkers on laptops, a whiteboard of "
        "algorithm diagrams, dual monitors of code, a campus lobby, an interview desk, or a handshake) "
        "so the subject is obvious at a glance. Put ONE main person in the foreground FACING the camera "
        "with ONE huge, instantly-readable emotion; their face is clearly visible, sharp, unobstructed, "
        "well-lit and the largest face in frame (close-up, waist-up, standing, or a medium shot all "
        "work). Set it in a BRIGHT, well-lit or DAYLIT space (big windows / bright office light) and "
        "light the face clean, bright and NEUTRAL — no colored gels, neon, or split light on the face; "
        "use vivid ACCENT colors for pop but AVOID a dark neon-night look. The hero in sharp "
        "focus, the setting in slightly softer focus (shallow depth of field) so it reads without "
        "clutter. Shot on a 50mm f/1.8 lens, natural realistic skin, true photographic detail — a REAL "
        "photo, NOT a 3D render, NOT CGI, NOT digital art, no plastic or over-smoothed skin. A clean, "
        "natural, symmetric face with normal undistorted features — exactly one pair of eyebrows and "
        "two eyes, no doubled/warped/extra facial features. Bright, not "
        "dark or muddy. Leave a calmer area for a large title overlay. Absolutely no text, letters, "
        "numbers, watermark, logos, UI, or real/famous people."
    ) + look


def _faceid_prompt(concept: str) -> str:
    """Build the FaceID image prompt from the script's per-video ``thumbnail_concept`` — high-CTR
    YouTube style, with the CONCEPT driving the scene (that is the dynamic, per-video part). Kept
    within CLIP's ~77-token budget (the SD1.5 text encoder truncates beyond it, so the concept leads).
    No baked-in text (the title is overlaid; the negative prompt drops stray text). To take full
    control, edit ``assets/thumbnail_prompt.txt`` and re-run ``content-foundry thumbnail``."""
    words = (concept or "a person reacting to a glowing screen").strip().rstrip(".").split()
    concept = " ".join(words[:18])  # the concept leads; ~18 words leaves room under CLIP's 77 tokens
    return (
        f"high-CTR YouTube thumbnail, real DSLR photo, {concept}, one prominent person facing camera in "
        "a relevant tech office / cubicles setting, huge readable emotion, face clearly visible and "
        "sharp, bright clean neutral light, colorful background, realistic skin, not a 3d render, no text"
    )


def _scene_brightness_score(png: bytes) -> float:
    """A 'is the subject brightly, cleanly lit?' proxy for ranking thumbnail scene candidates. Returns
    the mean LUMA of the center of the frame (where the face sits): a bright, clean face scores high,
    while a dark or heavy-neon face (blue/red gels read LOW in luma) scores low — so the best-lit
    candidate wins. Any decode problem scores 0 so it is never chosen over a valid image."""
    try:
        from PIL import Image, ImageStat

        img = Image.open(BytesIO(png)).convert("L")
        w, h = img.size
        center = img.crop((int(w * 0.28), int(h * 0.08), int(w * 0.72), int(h * 0.92)))
        return float(ImageStat.Stat(center).mean[0])
    except Exception:
        return 0.0


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
    def __init__(self, settings, image_provider=None, broll_client=None, llm_provider=None):
        self._settings = settings
        self._image = image_provider
        self._broll = broll_client
        self._llm = llm_provider
        self._relevance_context = ""
        self._log = get_logger(component="visuals")

    def run(
        self, run_id: str, script: Script, voiceover: VoiceoverAsset, *, run_root: Path
    ) -> VisualPackage:
        durations = {st.scene_index: (st.end - st.start) for st in voiceover.scene_timings}
        scenes_dir = run_root / "assets" / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        scene_visuals: list[SceneVisual] = []
        # A bag of words describing THIS video (every scene's directed B-roll queries). It is handed
        # to the stock search so clips whose tags touch nothing in this video (holiday/greeting/other
        # off-topic padding the API returns) are rejected. Off when too thin to be reliable.
        vocab = {
            w
            for scene in script.scenes
            for kw in scene.b_roll_keywords
            for w in re.findall(r"[a-z]{3,}", (kw or "").lower())
        }
        self._relevance_context = " ".join(sorted(vocab)) if len(vocab) >= 8 else ""
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

        # Thumbnail (also exposed as the standalone `thumbnail` command for quick regeneration).
        thumbnail_text = self.render_thumbnail(script, run_root=run_root)

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

    def render_thumbnail(self, script: Script, *, run_root: Path, prompt: str | None = None) -> str:
        """Compose ONLY the thumbnail and return its overlay text. Shared by the visuals stage and the
        standalone `thumbnail` command. The IMAGE prompt actually used is saved to
        ``assets/thumbnail_prompt.txt`` (human-editable) so you can tweak it and regenerate. Resolution
        order for the prompt: an explicit ``prompt`` argument > a saved (edited) prompt file > the
        auto-built prompt from the script's thumbnail_concept."""
        # The overlay text is its OWN punchy line (decoupled from the title); fall back to the first
        # title option when the writer didn't supply one.
        thumbnail_text = (script.thumbnail_text or "").strip() or _fallback_thumb_text(
            (script.title_options or [script.thumbnail_concept or "Career Advice"])[0]
        )
        # Hard cap the overlay to a few big words so it stays readable at thumbnail size (a wall of
        # text is the #1 thumbnail-CTR killer). The year is NOT auto-stamped onto the thumbnail — a
        # tacked-on "(2026)" clutters a punchy hook; the TITLE carries the year for search when the
        # topic is genuinely time-sensitive, and the writer only puts a year in the thumbnail_text
        # itself when the year IS the hook.
        thumbnail_text = _cap_words(thumbnail_text, self._settings.thumbnail_max_words)
        prompt_path = run_root / _THUMB_PROMPT_REL
        image_prompt = prompt
        if image_prompt is None and prompt_path.exists():
            image_prompt = prompt_path.read_text(encoding="utf-8").strip() or None
        used = self._compose_thumbnail(
            script.thumbnail_concept, thumbnail_text, run_root / _THUMB_REL,
            override_prompt=image_prompt, title=(script.title_options or [""])[0],
        )
        if used:  # persist the exact prompt used, so it can be inspected and edited for a re-run
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(used, encoding="utf-8")
        self._cap_thumbnail_bytes(run_root / _THUMB_REL)
        return thumbnail_text

    def _cap_thumbnail_bytes(self, path: Path, *, max_bytes: int = 1_900_000) -> None:
        """Keep the thumbnail under YouTube's 2 MB custom-thumbnail limit. A photo-real 9:16 PNG can
        exceed it, and then ``thumbnails.set`` fails silently (best-effort) so the video publishes with
        NO custom thumbnail. Re-save optimized; if still too big, downscale in steps until it fits.
        Best-effort, PNG in place."""
        try:
            if not path.exists() or path.stat().st_size <= max_bytes:
                return
            from PIL import Image

            img = Image.open(path)
            img.load()
            img.save(path, format="PNG", optimize=True)
            w, h = img.size
            while path.stat().st_size > max_bytes and min(w, h) > 320:
                w, h = int(w * 0.85), int(h * 0.85)
                img = img.resize((w, h), Image.LANCZOS)
                img.save(path, format="PNG", optimize=True)
            self._log.info("thumbnail_capped", bytes=path.stat().st_size, dims=f"{w}x{h}")
        except Exception as exc:  # best-effort: an oversize thumb beats crashing the visuals stage
            self._log.warning("thumbnail_cap_failed", error=str(exc))

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
                combined.extend(
                    self._broll.search(_search_terms(term), context=self._relevance_context)
                )
            except Exception as exc:  # a flaky search must not kill the scene
                self._log.warning("broll_search_failed", query=term, error=str(exc))
        return combined

    def _build_shots(
        self, scene, run_root: Path, *, duration: float, picker: _BrollPicker
    ) -> list[VisualShot]:
        """Break the scene into ordered visual beats — each matched to that moment — so the footage
        changes with the narration. A beat gets a relevant stock clip when one exists; when it does
        NOT, it gets a bespoke GENERATED image (a witty, on-topic prompt) instead of a borrowed
        off-topic clip — so no shot ever shows something irrelevant."""
        beats = [k.strip() for k in scene.b_roll_keywords if k and k.strip()]
        pace = _cut_pace(getattr(scene, "cut", None))  # the editor 'cut' hint steers shot density
        n = max(1, min(len(beats), int(duration // (_MIN_SHOT_SEC * pace)) or 1, _MAX_SHOTS_PER_SCENE))
        chosen = beats[:n]
        # First pass: try to claim a RELEVANT, fresh clip for each beat FROM ITS OWN search (not a
        # borrowed clip from another beat — that is exactly the "irrelevant shot" we want to avoid).
        clips = [picker.pick(self._broll_candidates([beat])) for beat in chosen]
        # Beats with no perfect clip -> generate an image. Craft all their prompts in ONE LLM call.
        gap_prompts = self._shot_image_prompts(
            scene, [b for b, u in zip(chosen, clips, strict=True) if not u]
        )
        found: list[tuple[str, str, str]] = []  # (rel_path, source, query)
        for j, (beat, url) in enumerate(zip(chosen, clips, strict=True)):
            stem = f"assets/scenes/scene_{scene.index}_shot_{j}"
            if url:
                rel = f"{stem}.mp4"
                (run_root / rel).write_bytes(self._broll.download(url))
                found.append((rel, _broll_source(url), beat))
            else:
                rel = f"{stem}.png"
                prompt = gap_prompts.get(beat) or build_image_prompt(
                    [beat], scene.on_screen_text, self._settings.visual_style
                )
                source = self._render_shot_image(
                    prompt, run_root / rel, caption=scene.on_screen_text or beat
                )
                found.append((rel, source, beat))
        if not found:
            return []
        per = round(duration / len(found), 3)  # split the scene evenly across the beats we found
        return [VisualShot(path=r, duration_sec=per, source=src, query=q) for r, src, q in found]

    def _shot_image_prompts(self, scene, beats: list[str]) -> dict[str, str]:
        """Witty, richly descriptive image prompts for the beats that got NO stock B-roll — one LLM
        call for the whole scene (grounded in its narration). Empty dict when off / no LLM / on any
        failure, so the caller uses its deterministic template."""
        if not beats or self._llm is None or not self._settings.scene_image_director_enabled:
            return {}
        try:
            from .scene_image_director import SceneImageDirector

            return SceneImageDirector(self._settings, self._llm).compose(
                beats=beats, narration=getattr(scene, "narration", "") or "",
                on_screen_text=scene.on_screen_text or "",
            )
        except Exception as exc:  # a prompt-writing failure must never break the visuals stage
            self._log.warning("scene_image_director_skipped", error=str(exc))
            return {}

    def _render_shot_image(self, prompt: str, target: Path, *, caption: str) -> str:
        """Generate a gap-fill image for one shot (at the full video resolution) and return its source
        label. A DESIGNED card is drawn when there is no image provider or generation fails, so a
        missing clip never leaves a hole."""
        target.parent.mkdir(parents=True, exist_ok=True)
        if self._image is not None:
            try:
                data = self._image.generate(prompt, size=self._settings.effective_resolution)
                target.write_bytes(data)
                return getattr(self._image, "name", self._settings.image_provider)
            except Exception as exc:  # a flaky image gen must not kill the scene
                self._log.warning("scene_shot_image_failed", error=str(exc))
        _write_card(caption or prompt, self._settings.resolution_wh, target)
        return "card"


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
            # Scene backgrounds fill the video frame, so generate at the EFFECTIVE video resolution
            # (vertical for a Short, 16:9 for long-form) — not the smaller thumbnail size — otherwise
            # every scene image is upscaled and looks soft. The card fallback renders at the same size.
            data = self._image.generate(prompt, size=self._settings.effective_resolution)
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
    def _avatar_thumbnail_path(self, emotion: str = "") -> Path | None:
        """The operator's avatar image for the thumbnail, or None when disabled/absent. Prefers an
        emotion-matched variant (``assets/avatar_<emotion>.png``) when one exists so the face's
        reaction fits the video, else falls back to the single base avatar."""
        if not self._settings.thumbnail_use_avatar:
            return None
        raw = (self._settings.avatar_image_path or "").strip()
        if not raw:
            return None
        base = Path(raw)
        if emotion:
            variant = base.with_name(f"{base.stem}_{emotion}{base.suffix}")
            if variant.exists():
                return variant
        return base if base.exists() else None

    def _compose_thumbnail(
        self, concept: str, text: str, target: Path, *, override_prompt: str | None = None,
        title: str = "",
    ) -> str | None:
        """Render the thumbnail and RETURN the image prompt actually used (so the caller can persist it
        to an editable file), or ``None`` when only the text card was drawn. ``override_prompt`` — a
        saved/edited prompt — is fed verbatim to the image model instead of the auto-built one, giving
        full manual control over the thumbnail."""
        size = self._settings.effective_thumbnail_wh
        avatar = self._avatar_thumbnail_path(_detect_emotion(concept))
        scene_cloud_failed = False  # the network image provider itself failed (outage) — skip re-tries
        if avatar is not None and self._settings.thumbnail_face_id_enabled:
            if self._settings.thumbnail_face_method == "swap":
                # Two-stage: a rich scene WITH a person from the normal image provider (LONG prompt, no
                # 77-token limit, so it follows the scene), then swap the operator's real face onto it.
                prompt = override_prompt or self._scene_prompt(concept, title, no_person=False)
                scene = self._best_scene(prompt)
                if scene and not self._settings.thumbnail_face_swap_enabled:
                    # Face-swap OFF (opt-out): use the AI-generated person AS-IS. Guided by
                    # AVATAR_APPEARANCE it already resembles the operator, with none of the swap /
                    # restore artifacts (e.g. doubled eyebrows), and it skips the whole GPU stack.
                    self._log.info("thumbnail_face_swap_disabled")
                    _write_card(text, size, target, base_png=scene, punchy=True)
                    return prompt
                if scene:
                    from ..providers.faceswap import swap_face

                    swapped = swap_face(self._settings, scene_png=scene, face_path=str(avatar))
                    if swapped:
                        _write_card(text, size, target, base_png=swapped, punchy=True)
                        return prompt
                else:
                    scene_cloud_failed = True  # provider outage — don't retry it in the fallback
                self._log.warning("faceswap_thumbnail_fell_back")
            else:
                # "generate": SD1.5 + IP-Adapter-FaceID in one local pass (bound by CLIP's 77 tokens).
                from ..providers.faceid import generate_face_image

                prompt = override_prompt or _faceid_prompt(concept)
                face_png = generate_face_image(
                    self._settings, prompt=prompt, face_path=str(avatar),
                    size=self._settings.effective_thumbnail_size,
                )
                if face_png:
                    _write_card(text, size, target, base_png=face_png, punchy=True)
                    return prompt
                self._log.warning("faceid_thumbnail_fell_back")
        # Fallback: background image + composited avatar cut-out (the default path). Skip re-calling a
        # provider that just failed (outage) so we go straight to the DESIGNED card instead of waiting.
        prepared = self._prepare_avatar(avatar) if avatar is not None else None
        prompt = override_prompt or self._scene_prompt(concept, title, no_person=prepared is not None)
        base = None if scene_cloud_failed else self._generate_image(prompt)
        # With no AI scene the face carries the designed card, so show it big; over a real scene it
        # stays a small corner tag.
        avatar_scale = (
            self._settings.thumbnail_avatar_scale if base is not None
            else max(self._settings.thumbnail_avatar_scale, 0.85)
        )
        _write_card(
            text, size, target, base_png=base, punchy=True,
            avatar_path=prepared, avatar_scale=avatar_scale,
        )
        return prompt if base is not None else None

    def _generate_image(self, prompt: str) -> bytes | None:
        """Generate a thumbnail-sized image from the configured provider; ``None`` if there is no
        provider or it fails (a thumbnail image failure must never crash the run)."""
        if self._image is None:
            return None
        try:
            return self._image.generate(prompt, size=self._settings.effective_thumbnail_size)
        except Exception as exc:
            self._log.warning("thumbnail_image_failed", error=str(exc))
            return None

    def _best_scene(self, prompt: str) -> bytes | None:
        """Generate several scene candidates and keep the BEST-LIT one. Pollinations/flux is high-
        variance — one call returns a clean, bright face and the next a dark, neon-muddy 'AI-slop' one
        — so picking the brightest of N reliably lands a scroll-stopping thumbnail (and a cleanly-lit
        face is also what the ONNX restorer needs to avoid artifacts). Costs N provider calls; 1 = off."""
        n = max(1, int(getattr(self._settings, "thumbnail_scene_candidates", 1)))
        if n == 1:
            return self._generate_image(prompt)
        best: bytes | None = None
        best_score = -1.0
        for _ in range(n):
            img = self._generate_image(prompt)
            if not img:
                continue
            score = _scene_brightness_score(img)
            if score > best_score:
                best, best_score = img, score
        if best is not None:
            self._log.info(
                "thumbnail_scene_selected", candidates=n, brightness=round(best_score, 1)
            )
        return best

    def _scene_prompt(self, concept: str, title: str, *, no_person: bool) -> str:
        """The thumbnail SCENE image prompt. When the thumbnail director is enabled and an LLM is
        available, an LLM writes a rich, per-video creative prompt (with a hard no-text rule);
        otherwise the built-in template is used. An explicit/saved prompt bypasses this upstream."""
        if self._llm is not None and self._settings.thumbnail_director_enabled:
            try:
                from .thumbnail_director import ThumbnailDirector

                directed = ThumbnailDirector(self._settings, self._llm).compose(
                    concept, title=title, niche=self._settings.target_niche, no_person=no_person
                )
            except Exception as exc:  # a thumbnail-prompt failure must never crash the run
                self._log.warning("thumbnail_director_skipped", error=str(exc))
                directed = None
            if directed:
                return directed
        return _thumbnail_prompt(
            concept, no_person=no_person,
            appearance=(self._settings.avatar_appearance or "").strip(),
        )

    def _prepare_avatar(self, path: Path) -> Path:
        """Return a TRANSPARENT-background avatar for the thumbnail. If the source already has real
        transparency, use it. Otherwise cut the background out with rembg (cached next to the source
        as ``<name>.cutout.png``); if rembg is missing or fails, keep the original and log a one-line
        hint so the thumbnail still renders (a face with a box beats no thumbnail)."""
        from PIL import Image

        try:
            if Image.open(path).convert("RGBA").split()[-1].getextrema()[0] < 250:
                return path  # already has meaningful transparency
        except Exception:
            return path
        cutout = path.with_name(f"{path.stem}.cutout.png")
        try:
            if cutout.exists() and cutout.stat().st_mtime >= path.stat().st_mtime:
                return cutout  # cached from a previous run
            from rembg import remove

            remove(Image.open(path)).save(cutout)  # RGBA with the background removed
            self._log.info("avatar_background_removed", cutout=str(cutout))
            return cutout
        except Exception as exc:
            self._log.info(
                "avatar_cutout_unavailable",
                hint="pip install rembg to auto-remove the background, or supply a transparent PNG",
                error=str(exc),
            )
            return path


# --------------------------------------------------------------------- Pillow
def _paste_avatar(img, avatar_path: Path, size_wh: tuple[int, int], scale: float = 0.9) -> float:
    """Composite the operator's face as a LARGE reaction figure flush to the RIGHT edge and bottom-
    anchored — the high-CTR "face on one side, text on the other" thumbnail layout (not a tiny corner
    badge). Uses the image's alpha when present (a transparent PNG cuts out cleanly); a broken file is
    skipped so a thumbnail never fails on it. ``scale`` = the face height as a fraction of the frame.
    Returns the fraction of the WIDTH the face occupies so the title can reserve the opposite side
    (0.0 when the image can't be loaded)."""
    from PIL import Image

    width, height = size_wh
    try:
        av = Image.open(avatar_path).convert("RGBA")
    except Exception:
        return 0.0
    target_h = int(height * min(max(scale, 0.3), 1.0))
    target_w = int(av.width * target_h / max(av.height, 1))
    max_w = int(width * 0.55)  # the face may own up to ~55% of the width, never the whole frame
    if target_w > max_w:
        target_w, target_h = max_w, int(av.height * max_w / max(av.width, 1))
    av = av.resize((max(1, target_w), max(1, target_h)))
    img.paste(av, (width - target_w, height - target_h), av)  # flush bottom-right
    return target_w / width


def _write_card(
    text: str, size_wh: tuple[int, int], target: Path, base_png: bytes | None = None,
    *, punchy: bool = False, avatar_path: Path | None = None, avatar_scale: float = 0.5,
):
    """Render a title card. ``punchy`` = a high-CTR YouTube-thumbnail overlay (bold UPPERCASE, a dark
    bottom scrim, a drop shadow + heavy outline, and numbers/the key word highlighted); otherwise a
    clean caption card (accent bar + centered shadowed text) used for scene fallbacks. ``avatar_path``
    composites the operator's face onto the frame before the title is drawn."""
    from PIL import Image, ImageDraw

    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = size_wh
    if base_png:
        img = Image.open(BytesIO(base_png)).convert("RGB").resize(size_wh)
        if punchy:
            img = _punch(img)  # saturation + contrast + sharpness so the scene POPS at small size
            img = Image.blend(img, Image.new("RGB", size_wh, (8, 11, 20)), 0.06)  # keep it BRIGHT
        else:
            img = Image.blend(img, Image.new("RGB", size_wh, (8, 11, 20)), 0.28)
    elif punchy:
        img = _thumbnail_fallback_bg(size_wh)  # no AI image -> a DESIGNED thumbnail, never empty
    else:
        img = _gradient_bg(size_wh)

    occupied = 0.0
    if avatar_path is not None:
        occupied = _paste_avatar(img, avatar_path, size_wh, avatar_scale)

    if punchy:
        _draw_punchy_title(img, text or "", size_wh, reserve_right=occupied)
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


def _draw_punchy_title(
    img, text: str, size_wh: tuple[int, int], *, reserve_right: float = 0.0
) -> None:
    """High-CTR thumbnail text: big bold UPPERCASE, bottom-anchored over a dark scrim, a drop shadow
    + heavy outline, and the numbers (or the single strongest word) in a punchy accent color.
    ``reserve_right`` keeps the words clear of a face/figure occupying that fraction of the right."""
    from PIL import ImageDraw

    width, height = size_wh
    draw = ImageDraw.Draw(img, "RGBA")
    title = " ".join((text or "").upper().split()) or "WATCH THIS"
    margin = int(width * 0.06)
    # Reserve the right side (where the face sits) so the words never overlap it.
    text_w = int(width * (1.0 - min(max(reserve_right, 0.0), 0.7)))
    box_w = max(int(width * 0.25), text_w - 2 * margin)

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


def _punch(img):
    """A 'scroll-stopping' pop for an AI thumbnail scene: FIRST lift a dark/neon exposure toward a
    bright target (flux frequently returns dim scenes that die in the feed), then add saturation,
    contrast, and sharpness so it reads bright and crisp at tiny thumbnail size."""
    from PIL import ImageEnhance, ImageStat

    # Exposure lift: best-of-N already keeps the brightest candidate, but when EVERY candidate came
    # back dark (flux is stubborn), brighten toward a bright target so the thumbnail is NEVER dim.
    # Capped so highlights don't blow out; a no-op once the scene is already bright.
    mean = ImageStat.Stat(img.convert("L")).mean[0] or 1.0
    if mean < 130.0:
        img = ImageEnhance.Brightness(img).enhance(min(2.3, 130.0 / mean))
    img = ImageEnhance.Color(img).enhance(1.3)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Sharpness(img).enhance(1.6)
    return img


def _thumbnail_fallback_bg(size_wh: tuple[int, int]):
    """A DESIGNED thumbnail background for when no AI image is available (all providers down): a bold
    gradient with soft accent glows, a faint tech dot-grid and an accent wedge — so a provider outage
    still yields a full, intentional thumbnail instead of an empty dark frame."""
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size_wh
    top, bot = (30, 41, 82), (5, 8, 20)  # deep indigo -> near-black
    img = Image.new("RGB", size_wh)
    draw = ImageDraw.Draw(img)
    span = max(1, height - 1)
    for y in range(height):
        t = y / span
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)),
        )
    # Soft accent glows for depth (blurred), composited over the gradient.
    over = Image.new("RGBA", size_wh, (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    od.ellipse([-width * 0.25, -height * 0.45, width * 0.55, height * 0.55], fill=(37, 99, 235, 150))
    od.ellipse([width * 0.55, height * 0.15, width * 1.2, height * 1.05], fill=(13, 110, 138, 95))
    over = over.filter(ImageFilter.GaussianBlur(radius=int(min(width, height) * 0.14)))
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    # Faint tech dot-grid fills the negative space so the frame never reads as empty.
    step = max(18, int(min(width, height) * 0.055))
    for yy in range(step, height, step):
        for xx in range(step, width, step):
            draw.ellipse([xx - 2, yy - 2, xx + 2, yy + 2], fill=(150, 180, 236, 22))
    # A bold accent wedge + bright edge, bottom-left, anchors the composition.
    draw.polygon(
        [(0, height), (0, int(height * 0.55)), (int(width * 0.42), height)], fill=(56, 189, 248, 34)
    )
    draw.line(
        [(0, int(height * 0.55)), (int(width * 0.42), height)],
        fill=(56, 189, 248, 130), width=max(2, width // 360),
    )
    return img


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
