"""Typed application configuration (Ch. 6).

A single :class:`Settings` object loads every value from environment variables / ``.env``.
No secret is ever hard-coded. Cross-field validators fail fast on inconsistent setups.

Accessed everywhere via :func:`get_settings` (an ``lru_cache``'d singleton — functionally the
"one settings object everyone shares" the spec calls for, but lazy so importing this module has
no side effects, which keeps tests hermetic).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError

VALID_SOURCES = {"adzuna", "layoffs", "news", "bls", "search"}
VALID_EVENTS = {
    "run_complete",
    "need_validation",
    "video_uploaded",
    "low_credits",
    "run_failed",
}
_SECRET_SUFFIXES = ("_key", "_token", "_secret", "_password")

# Suggested presets selectable via ``--profile`` (Ch. 6.5).
PROFILES: dict[str, dict[str, object]] = {
    "cheap": {"judge_mode": "deterministic", "image_provider": "none", "max_revisions": 1},
    "quality": {"judge_mode": "hybrid", "image_provider": "openai", "max_revisions": 3},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- LLM Providers ----------
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""  # Google AI Studio (Gemini): https://aistudio.google.com/apikey
    primary_provider: Literal["anthropic", "openai", "google", "local"] = "anthropic"
    fallback_provider: Literal["anthropic", "openai", "google", "local", "none"] = "openai"
    generator_model: str = "claude-sonnet-4-20250514"
    judge_model: str = "claude-sonnet-4-20250514"
    llm_temperature: float = 0.7
    judge_temperature: float = 0.0
    llm_max_tokens: int = 4096
    # Nucleus-sampling cap sent to the cloud LLMs (currently Google). 0.95 keeps outputs coherent
    # while still allowing variety — it is also Gemini's own default. Lower it for tighter output.
    llm_top_p: float = Field(0.95, ge=0, le=1)
    # Tiered model routing (future plan 2): heavy for hard creative work, light for
    # mechanical / high-volume calls. Empty => fall back to generator/judge models.
    llm_tiering_enabled: bool = True
    model_heavy: str = ""
    model_light: str = ""
    # Local / self-hosted LLM (cost saver): any OpenAI-compatible server (Ollama, LM Studio,
    # vLLM, llama.cpp, LocalAI). Active when PRIMARY/FALLBACK_PROVIDER=local; LOCAL_LLM_MODEL is
    # used for every call (the cloud GENERATOR/JUDGE/heavy/light model names are ignored).
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "llama3.1"
    local_llm_api_key: str = "local"
    # Google AI Studio (Gemini). GOOGLE_MODELS is a comma-separated, best-first list of Gemini models
    # tried IN ORDER: on ANY error (quota/429, bad id/404, network) the chain moves to the NEXT model,
    # so one model's daily free-tier quota running out doesn't sink the run (each model has its OWN
    # quota pool). Only after the whole list is exhausted does it fall through to FALLBACK_PROVIDER
    # (e.g. local). Per-call tiering model names are ignored (like local).
    google_models: str = "gemini-2.5-flash,gemini-2.5-flash-lite"
    # "Thinking" (extended reasoning) for Gemini 2.5+/3.x calls. On => a `thinkingConfig` (dynamic
    # budget) is sent AND "[THINK]" is prepended to the system prompt, so the model reasons before it
    # answers (higher quality, a few more tokens). Ignored for Gemma (no thinking mode). Needs a
    # roomy LLM_MAX_TOKENS so thinking doesn't crowd out the answer.
    google_thinking: bool = True

    # ---------- Data Sources ----------
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    newsapi_key: str = ""
    enabled_sources: str = "search"
    layoffs_feed_url: str = ""
    signal_cache_ttl_min: int = 720
    # Web search is the DEFAULT source — domain-agnostic, it queries the run's topic directly so any
    # niche works out of the box. Free via DuckDuckGo (no key); set SEARCH_PROVIDER=tavily|brave + its
    # key for a real index. The job/layoff/news feeds are career-specific opt-ins (add them to
    # ENABLED_SOURCES only for job-market videos).
    search_provider: Literal["duckduckgo", "tavily", "brave"] = "duckduckgo"
    tavily_api_key: str = ""
    brave_api_key: str = ""
    search_max_results: int = Field(8, ge=1, le=25)
    # Multi-query fan-out: instead of a single web search, run the base topic query PLUS several
    # facet-augmented variants (e.g. "<topic> salary", "<topic> statistics") and merge + dedupe the
    # hits. This surfaces far more distinct, number-rich results, so the brief does not collapse to a
    # couple of near-duplicate headlines. SEARCH_QUERY_COUNT caps the TOTAL queries (base + facets);
    # SEARCH_FACETS is the ordered pool of angle suffixes to draw from.
    search_query_count: int = Field(4, ge=1, le=10)
    search_facets: str = "statistics,salary,trends 2026,common mistakes,requirements,tips"
    # Drop web-search hits that share NO meaningful word with the run's topic. A generic angle like
    # "trends 2026" can otherwise pull "Fashion Trends Tokyo 2026" or "Blox Fruits Values 2026" into
    # the brief and weaken the script. Deterministic keyword-overlap filter (no LLM). Off => keep all.
    search_relevance_filter: bool = True

    # ---------- Research (Agent 1.5) ----------
    # After the idea is chosen, an LLM reads the real pages behind the brief's sources and synthesizes
    # a DEPTH report (mechanisms: how/why) so the script can EXPLAIN, not just list tips. Off => the
    # generator works from the data brief alone. Grounded in fetched pages; failures degrade to snippets.
    research_enabled: bool = True
    research_max_sources: int = Field(4, ge=1, le=12)
    # Over-fetch this many EXTRA candidate pages, then keep only the most on-topic
    # research_max_sources (a relevance buffer so a weak/paywalled fetch doesn't waste a slot).
    research_source_buffer: int = Field(2, ge=0, le=8)
    research_max_points: int = Field(6, ge=1, le=20)
    research_max_chars_per_source: int = Field(4000, ge=500, le=20000)
    research_fetch_timeout_sec: float = Field(10.0, ge=1, le=60)

    # ---------- Pipeline Behaviour ----------
    max_revisions: int = 3
    judge_mode: Literal["hybrid", "deterministic", "llm"] = "hybrid"
    # All judge scores are on a 0-5 scale (matching the LLM's native 1-5 grading), so every floor
    # below is 0-5 too and weighted_total is a 0-5 weighted average. The LLM dims are DISCRETE 1-5, so
    # keep insight/wittiness floors <= 4.0 (a floor above 4 would need a perfect 5, i.e. unreachable).
    pass_threshold: float = Field(3.75, ge=0, le=5)
    insight_min: float = Field(3.5, ge=0, le=5)
    wittiness_min: float = Field(2.5, ge=0, le=5)
    ending_min: float = Field(3.0, ge=0, le=5)
    grounding_min: float = Field(4.0, ge=0, le=5)
    # Two scenes whose narration is more similar than this (3-gram Jaccard) are treated as duplicate
    # padding and force a REVISE — stops the model recycling the same lines/facts across scenes.
    max_scene_similarity: float = Field(0.5, ge=0, le=1)
    fatigue_lookback: int = 5
    target_niche: str = "tech careers"
    # Brainstormer (Agent 0): an LLM proposes a fresh, specific video idea each run to avoid topic
    # collapse; falls back to a deterministic content angle. Disable to reuse the raw topic/niche.
    brainstorm_enabled: bool = True
    brainstorm_idea_count: int = Field(5, ge=1, le=10)
    script_target_words: int = 900
    min_facts: int = 3
    # Upper bound on grounded facts distilled into the brief. A richer, multi-query search yields many
    # distinct signals; keeping more of them (not just 8) gives the script more distinct angles to draw
    # on, so it never has to pad by recycling the same one or two numbers across scenes.
    max_facts: int = Field(12, ge=1, le=50)
    # Completeness gate (Ch. 9.3a): reject stub scripts the quality rubric would otherwise pass. A
    # grounded single-scene draft scores well on every dimension but is far too short for a video.
    min_scenes: int = Field(3, ge=1)
    min_script_word_ratio: float = Field(0.5, ge=0, le=1)
    # A genuinely excellent draft (weighted_total >= gate_relief_score) earns `gate_relief_ratio`
    # slack on the insight & length floors ONLY — never on grounding, compliance, or fatigue.
    # Set gate_relief_score > 5 to disable.
    gate_relief_score: float = Field(4.5, ge=0, le=6)
    gate_relief_ratio: float = Field(0.20, ge=0, le=0.5)
    # 0 = disabled (default). When > 0, abort the revision loop once a script still scores below
    # this weighted total (0-5) on attempt >= 2 — it can't realistically reach PASS, so stop paying.
    fail_fast_score: float = Field(0.0, ge=0, le=5)
    # Human-in-the-loop: when true, a PASSed script pauses before production (voiceover onward) so you
    # can review script.json, then `content-foundry resume` to continue. Default off (fully automatic).
    require_script_approval: bool = False

    # ---------- Voiceover (TTS) ----------
    tts_provider: Literal["elevenlabs", "openai", "edge", "piper", "chatterbox"] = "elevenlabs"
    elevenlabs_api_key: str = ""
    tts_voice_id: str = "Rachel"
    # Alternate the narrator by run-id parity: male voice for ODD run ids, female for EVEN. Leave
    # both blank to always use tts_voice_id. Values are your TTS provider's own voice names.
    tts_voice_male: str = ""
    tts_voice_female: str = ""
    tts_model: str = "eleven_multilingual_v2"
    tts_format: str = "mp3_44100_128"
    # Free voices: edge = Microsoft neural (online, no key); piper = fully offline (needs a .onnx model).
    piper_model_path: str = ""
    piper_executable: str = "piper"
    # Free zero-shot VOICE CLONING (chatterbox, MIT => safe for a monetized channel): point at a short
    # (~15-30s) clean WAV of YOUR voice; cloned locally (GPU recommended). pip install chatterbox-tts.
    tts_reference_clip: str = ""
    tts_clone_device: str = "auto"  # auto | cuda | cpu
    tts_clone_exaggeration: float = Field(0.5, ge=0.0, le=2.0)  # 0.5 neutral; higher = more expressive
    tts_clone_cfg: float = Field(0.5, ge=0.0, le=1.0)  # lower (~0.3) = steadier, reference-paced speech
    # Silence kept on EACH side of a synthesized chunk when trimming Chatterbox's dead air. Too small
    # and sentence-to-sentence transitions feel abrupt/clipped; ~150 ms leaves a natural breath so the
    # stitched narration flows instead of jump-cutting between sentences.
    tts_silence_pad_ms: int = Field(150, ge=0, le=1000)

    # ---------- Visuals ----------
    image_provider: Literal["openai", "stability", "google", "pollinations", "none"] = "openai"
    # Optional fallback image provider, used only when the primary fails (paid-plan, quota, outage).
    # e.g. IMAGE_PROVIDER=google + IMAGE_FALLBACK_PROVIDER=pollinations = Imagen with a FREE safety net.
    image_fallback_provider: Literal[
        "openai", "stability", "google", "pollinations", "none"
    ] = "none"
    # Google image model when IMAGE_PROVIDER=google (uses GOOGLE_API_KEY). Nano Banana
    # (gemini-2.5-flash-image) is the durable default; imagen-4.0-ultra-generate-001 (and -std/-fast)
    # also work but Imagen is deprecated (shuts down 2026-08-17).
    google_image_model: str = "gemini-2.5-flash-image"
    stability_api_key: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""  # optional 2nd free B-roll source (more variety across videos)
    coverr_api_key: str = ""  # optional 3rd free B-roll source (coverr.co; request a key + attribution)
    # How many candidate clips to pull per B-roll query so each scene can get many distinct clips
    # (every clip is used at most once, so a bigger pool = shorter, more varied beats — no stretching
    # one clip). Pexels allows up to 80 per page.
    broll_pool_size: int = Field(24, ge=1, le=80)
    # LLM "visual director" (Agent 5.5): after the script is written, re-derive each scene's B-roll
    # search queries from the WHOLE script so the footage is both relevant to the scene AND visually
    # diverse across the video (no repeated shots). Uses the configured LLM (e.g. Gemini); best-effort,
    # falls back to the generator's keywords on any failure.
    broll_director_enabled: bool = True
    broll_director_max_queries: int = Field(8, ge=1, le=12)
    # GAP-FILL IMAGES (Agent 5.7): when a shot gets NO relevant stock B-roll, GENERATE a bespoke image
    # for it instead of borrowing an off-topic clip. An LLM art-director writes a vivid, witty, richly
    # descriptive prompt from the shot's beat + the scene's narration; best-effort, falling back to the
    # deterministic template when no LLM is configured or the call fails. The image is always generated
    # on a gap — this flag only controls the LLM prompt smartness.
    scene_image_director_enabled: bool = True
    # THUMBNAIL DIRECTOR (Agent 5.6): an LLM writes a rich, per-video thumbnail image-generation prompt
    # from the script's concept/title/niche (quality over the generic static template), with a hard
    # NO-TEXT instruction so the image model stops baking in gibberish "hieroglyph" lettering.
    # Best-effort: falls back to the built-in template on any failure. Default ON (quality); the prompt
    # is saved to assets/thumbnail_prompt.txt so you can edit it and re-run `content-foundry thumbnail`.
    thumbnail_director_enabled: bool = True
    visual_style: str = "clean infographic, high-contrast, bold text"
    scenes_per_video: int = 10
    thumbnail_size: str = "1280x720"
    shorts_thumbnail_size: str = "1080x1920"  # vertical 9:16 thumbnail for a Short (matches the frame)
    # SHORTS ONLY: bake the designed thumbnail as a brief FROZEN opening frame of the Short (with
    # silent audio) so it becomes a real video FRAME — the only way to control a Short's thumbnail
    # (YouTube ignores a custom uploaded thumbnail for Shorts). It doubles as a bold hook card. ~0.5s
    # balances "reliably a selectable/auto-sampled frame" against retention; 0 = off. Best-effort.
    shorts_thumbnail_intro_sec: float = Field(0.5, ge=0, le=3)
    # ---------- Face-identity thumbnail (option B: YOUR face, generated not pasted) ----------
    # Generate the thumbnail WITH the operator's own face + the prompt's emotion in one local model
    # pass (SD1.5 + IP-Adapter-FaceID on the GPU) instead of compositing a cut-out. OFF by default; it
    # needs a one-time install (see providers/faceid.py) and falls back to the composited thumbnail
    # whenever the model/deps are unavailable, so nothing ever breaks.
    thumbnail_face_id_enabled: bool = False
    # HOW to put your face in. "swap" (recommended): generate a rich scene with a LONG prompt via the
    # normal image provider (Pollinations/Imagen — NO 77-token limit, so it follows the scene
    # instructions), then swap YOUR real face onto the person with insightface's inswapper (high
    # identity fidelity). "generate": the older SD1.5 + IP-Adapter-FaceID single pass (bound by CLIP's
    # 77 tokens, weaker instruction-following). Either falls back to the composited thumbnail if its
    # models are unavailable, so nothing ever breaks.
    thumbnail_face_method: Literal["swap", "generate"] = "swap"
    # Master switch for the FACE-SWAP + restore stages (opt-out). true = swap the operator's real face
    # onto the generated person (+ optional restore). FALSE = use the AI-generated person AS-IS — with
    # AVATAR_APPEARANCE it already resembles the operator and avoids swap/restore artifacts (e.g.
    # doubled eyebrows). Scene generation, text, and the punch pass are unchanged either way.
    thumbnail_face_swap_enabled: bool = True
    faceswap_model_path: str = ""  # inswapper_128.onnx path; blank => auto-locate/download (~530 MB)
    # After the swap, RESTORE/upscale the soft 128 px swapped face with an offline ONNX enhancer
    # (GFPGAN/CodeFormer/GPEN) so it looks sharp and REAL instead of AI-soft. Aligns to the FFHQ/512
    # template with a RANSAC fit and blends the crop back through a feathered box mask (the facefusion
    # method) so it no longer cracks or seams. Best-effort + OOM-safe (frees the swapper's GPU memory
    # first); ANY problem keeps the un-restored swap. Model auto-downloads (~325 MB).
    thumbnail_face_restore: bool = True
    face_restore_model: Literal["gfpgan_1.4", "codeformer", "gpen_bfr_512"] = "gfpgan_1.4"
    face_restore_model_path: str = ""  # explicit .onnx path; blank => auto-download by face_restore_model
    # QUALITY vs TIME: generate this many thumbnail SCENE candidates and keep the best-lit one (the
    # brightest, clearest face) before the face-swap. Pollinations/flux is high-variance — some scenes
    # come out dark / neon-muddy ("AI slop") — so best-of-N reliably lands a bright, clean, high-CTR
    # thumbnail (a cleanly-lit face is also what the restorer needs). 1 = off. Costs N provider calls.
    thumbnail_scene_candidates: int = Field(3, ge=1, le=6)
    faceid_base_model: str = "SG161222/Realistic_Vision_V5.1_noVAE"  # any photoreal SD1.5 checkpoint
    faceid_ip_repo: str = "h94/IP-Adapter-FaceID"
    faceid_ip_weight: str = "ip-adapter-faceid_sd15.bin"
    faceid_scale: float = Field(0.6, ge=0.0, le=1.5)  # identity strength (higher = more like you)
    faceid_steps: int = Field(30, ge=10, le=60)
    faceid_guidance: float = Field(7.5, ge=1.0, le=15.0)
    faceid_gen_size: str = "768x448"  # SD-friendly generation size, upscaled to THUMBNAIL_SIZE
    faceid_device: str = "auto"  # auto | cuda | cpu
    faceid_negative_prompt: str = "multiple people, two people, extra person, crowd, text, watermark, logo, extra fingers, deformed, blurry, low quality, 3d render, cgi, plastic skin, waxy skin, airbrushed, over-smooth, illustration, cartoon, painting, oversaturated, uncanny"

    # ---------- Content format (long-form video vs vertical Shorts) ----------
    # ONE switch selects the whole output shape. "long" = the standard 16:9 long-form video (every
    # long-form field is used exactly as before, so existing runs are unaffected). "short" = a vertical
    # 9:16 YouTube Short built end-to-end by the SAME pipeline: one tight ~50s idea with big burned-in
    # captions. Format-specific values live in the SHORTS_* fields and are chosen by the effective_*
    # properties, so switching back to "long" restores the exact prior behaviour.
    content_format: Literal["long", "short"] = "long"
    shorts_resolution: str = "1080x1920"  # vertical 9:16 (portrait)
    shorts_target_words: int = Field(100, ge=40, le=600)  # ~35-45s of narration (Shorts retain best short)
    shorts_scenes: int = Field(4, ge=2, le=12)
    shorts_max_duration_sec: float = Field(50.0, ge=15.0, le=180.0)  # target ceiling (hard max is 3 min)
    shorts_burn_captions: bool = True  # captions are near-mandatory on muted, fast-scrolled Shorts
    shorts_intro_enabled: bool = False  # skip the fixed tagline; a Short must hook in the first second
    shorts_scene_transition: Literal[
        "none", "fade", "fadewhite", "fadeblack", "dissolve",
        "smoothleft", "smoothright", "circleopen", "radial", "wipeleft", "slideleft",
    ] = "none"  # fast hard cuts read better than slow blends on a Short
    shorts_hashtag: str = "#Shorts"  # appended to the description so YouTube classifies it as a Short

    # ---------- Render ----------
    render_backend: Literal["ffmpeg", "moviepy", "avatar"] = "ffmpeg"
    ffmpeg_path: str = ""  # optional explicit path to ffmpeg(.exe); auto-discovered when blank
    # Video encoder. "auto" uses your GPU's hardware H.264 encoder when ffmpeg exposes one (NVIDIA
    # h264_nvenc, then Intel h264_qsv, then AMD h264_amf) — far faster than the CPU libx264 — and
    # falls back to libx264 automatically if the GPU encode fails. Force one by name to override
    # ("libx264" = CPU, "h264_nvenc", "hevc_nvenc", "h264_qsv", "h264_amf").
    video_encoder: str = "auto"
    avatar_provider: Literal["none", "heygen", "did"] = "none"
    heygen_api_key: str = ""
    video_resolution: str = "1920x1080"
    video_fps: int = 30
    # Play the whole output faster/slower (1.5 = 1.5x). Audio pitch is preserved; captions stay in sync.
    video_speed: float = Field(1.0, ge=0.25, le=4.0)
    # Burn narration subtitles into the video. OFF by default: YouTube auto-generates closed captions
    # (synced to the real audio, free, auto-translated), and a burned track only stays in sync when the
    # TTS reports real word timings (ElevenLabs/Edge) — Chatterbox/Piper/OpenAI even-split and drift.
    # Enable only with a timing-capable voice. Source citations are a separate track, always burned.
    captions_enabled: bool = False
    # On-screen source citation (top strip): seconds it stays up from the moment the stat is spoken
    # before it disappears — a brief glance, not a permanent watermark.
    citation_seconds: float = Field(7.0, ge=3.0, le=15.0)
    render_fallback: bool = True
    # Cross-blend consecutive scenes instead of hard cuts (ffmpeg xfade). "none" = hard cut.
    # "fade" is a smooth crossfade; "fadewhite" flashes through white for a lighter feel.
    scene_transition: Literal[
        "none", "fade", "fadewhite", "fadeblack", "dissolve",
        "smoothleft", "smoothright", "circleopen", "radial", "wipeleft", "slideleft",
    ] = "none"
    scene_transition_sec: float = Field(0.5, ge=0.1, le=2.0)
    # Warm the whole video (0 = neutral/off, 1 = strongly warm). Pushes mids/highlights toward amber.
    color_warmth: float = Field(0.0, ge=0.0, le=1.0)
    # A small "Subscribe" badge that fades in at the video's midpoint for a few seconds.
    subscribe_nudge_enabled: bool = False
    subscribe_nudge_sec: float = Field(4.0, ge=1.0, le=12.0)
    subscribe_nudge_position: Literal[
        "top-left", "top-right", "bottom-left", "bottom-right", "top-center", "bottom-center"
    ] = "bottom-center"
    # Ring a short notification bell (resolved from sfx_dir, e.g. bell.mp3) the instant the Subscribe
    # badge fades in. It rides the sound-effects mixer, so it needs sfx_enabled; set the keyword to
    # any file in sfx_dir ("bell" -> bell.mp3), or disable it to show the badge silently.
    subscribe_bell_enabled: bool = True
    subscribe_bell_sound: str = "bell"
    # A small, softly GLOWING "Like" badge (blue thumbs-up) that fades in once early in the video —
    # a gentle nudge to hit like. Independent of the Subscribe badge (shown at a different moment).
    like_nudge_enabled: bool = False
    like_nudge_sec: float = Field(4.0, ge=1.0, le=12.0)
    like_nudge_position: Literal[
        "top-left", "top-right", "bottom-left", "bottom-right", "top-center", "bottom-center"
    ] = "bottom-center"
    # Where in the runtime the badge appears, as a fraction (0.25 = a quarter in) — kept clear of the
    # midpoint Subscribe badge.
    like_nudge_at: float = Field(0.25, ge=0.05, le=0.9)
    # Bake a neon glow halo behind the badge and let it gently "breathe" (a size pulse) while visible.
    like_nudge_glow: bool = True
    like_nudge_pulse: float = Field(0.06, ge=0.0, le=0.4)  # size-pulse amplitude (0 = steady)

    # A fixed, channel-signature opening line the narrator always says first. Prepended to the first
    # scene in code so every video opens the same way on ANY topic — set it to your own catchphrase,
    # or disable it to let each script open on its own first line.
    intro_enabled: bool = True
    intro_tagline: str = "No fluff, let's get straight into it."
    # Your real background/credentials, woven SUBTLY into the narration for authority (blank = fully
    # generic). Injected at runtime so the shipped prompt files stay generic. E.g. "AI Scientist at X".
    creator_bio: str = ""
    # Optional SHORT credibility tag some titles/thumbnails may carry (e.g. "FAANG AI Scientist").
    # Blank = the writer may infer a short one from creator_bio, or omit it. Used only sometimes.
    creator_title_tag: str = ""

    # ---------- Sound effects ----------
    # Script-authored SFX cues mixed into the narration at each scene's start. Local library first,
    # then an optional Freesound download (Pixabay has no SFX API — drop those files into sfx_dir).
    sfx_enabled: bool = False
    sfx_dir: str = "data/sounds"
    freesound_api_key: str = ""
    sfx_volume_db: float = Field(-4.0, ge=-40.0, le=6.0)

    # Personal avatar overlay (future plan 1): composited at a fixed corner of every frame.
    # The image is operator-supplied; rendering skips gracefully when the file is absent.
    avatar_overlay_enabled: bool = False
    avatar_image_path: str = "assets/avatar.png"
    avatar_position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "bottom-right"
    avatar_scale: float = Field(0.18, gt=0, le=1)
    avatar_margin: int = Field(24, ge=0)
    # Composite the avatar image (your face) into the THUMBNAIL as the human element instead of an
    # AI-generated face — a consistent real face lifts click-through. Skipped when the file is absent.
    thumbnail_use_avatar: bool = True
    thumbnail_avatar_scale: float = Field(0.9, ge=0.15, le=1.0)  # face height as a fraction of the thumb (big = main-region reaction, flush right)
    # A short physical description of YOU (whose face is swapped into the thumbnail), e.g. "a bearded
    # South Asian man in his late 20s with short dark hair and glasses". Injected into the thumbnail's
    # person prompt so the AI generates someone who ALREADY resembles you — the face-swap only
    # transplants the inner face and keeps the generated head's age/gender/hair, so a matching base is
    # what makes the swap actually look like you. Blank => a generic person (much weaker likeness).
    avatar_appearance: str = ""

    # ---------- Publishing (YouTube) ----------
    youtube_client_secrets_file: str = "secrets/client_secrets.json"
    youtube_token_file: str = "secrets/youtube_token.json"
    publish_mode: Literal["draft", "auto"] = "draft"
    youtube_privacy_status: Literal["private", "unlisted", "public"] = "private"
    youtube_category_id: str = "22"
    youtube_default_language: str = "en"
    # Buffer (seconds) to wait AFTER the upload finishes BEFORE setting the custom thumbnail. A
    # just-uploaded video is still processing and YouTube rejects thumbnails.set for a while, so this
    # head start (plus an automatic retry-with-backoff) makes the thumbnail reliably stick. Raise it
    # if your videos take longer to process; 0 = attempt immediately and rely on the retry only.
    publish_thumbnail_delay_sec: float = Field(15.0, ge=0, le=300)
    # YouTube SHORTS take their thumbnail from a video FRAME — a custom thumbnails.set is ignored (the
    # custom-Short-thumbnail option is a limited, mobile-only rollout, not in the Data API). OFF => skip
    # the futile call on a Short (long-form videos always get their custom thumbnail). Turn ON only if
    # your account actually has custom Short thumbnails.
    publish_shorts_custom_thumbnail: bool = False
    # Optional: auto-add each upload to this playlist (a niche SERIES boosts session watch time, a top
    # ranking signal). Create it once in YouTube Studio and paste its id (starts "PL..."). Blank => skip.
    youtube_playlist_id: str = ""
    require_manual_disclosure_before_public: bool = True
    # For every published video, write end_screen.json listing the N most topically-related PRIOR
    # videos (name + link) so you can set the 1+1 end screen manually — the Data API can't set end
    # screens. The candidate pool is your local run history (so unlisted uploads count too).
    end_screen_enabled: bool = True
    end_screen_count: int = Field(2, ge=1, le=4)
    # Also POST those end-card picks (the most related PRIOR videos on your channel) as a "watch next"
    # top comment right after publishing — pulls viewers to more of your videos on BOTH Shorts and
    # long-form. Uses the same comment channel as PUBLISH_TOP_COMMENT (needs the youtube.force-ssl
    # scope, requested when EITHER is on), and posts on its own even if PUBLISH_TOP_COMMENT is off.
    # Best-effort; skipped when there are no prior published videos yet. Pick count reuses
    # END_SCREEN_COUNT so the comment matches the end screen exactly.
    recommend_comment_enabled: bool = True
    recommend_comment_header: str = "More videos you might like:"
    # Pull viewers deeper into the channel. When enabled, a short CTA block (subscribe + a link to
    # explore the rest of the channel) is appended to EVERY description (long and Short). Set
    # YOUTUBE_CHANNEL_URL to your channel/handle URL; blank => a generic subscribe line.
    channel_cta_enabled: bool = True
    youtube_channel_url: str = ""  # e.g. https://www.youtube.com/@YourHandle
    channel_cta_text: str = "Subscribe for more, and explore the channel for the full deep-dive videos."
    # Best-effort: post ONE top-level comment (the channel CTA) on each upload via the Data API. Needs
    # the youtube.force-ssl scope, so turning this ON requires deleting the OAuth token to re-consent
    # (see Human_Tasks). The API cannot PIN a comment — pin it manually in Studio. Default off.
    publish_top_comment: bool = False
    # ---------- Affiliate links (optional monetization) ----------
    # OFF by default. When on, topic-relevant resource links + a required disclosure are appended to
    # every description (and, when PUBLISH_TOP_COMMENT is on, the comment). The platform catalog (what
    # each is good for + its topics) is built in; you paste ONLY your referral URL / tag per platform
    # below (blank => that platform is skipped) — no per-video product curation. See Human_Tasks for
    # how to get each program's link. Amazon products are found via the real search provider (a genuine
    # product URL + your associate tag), never invented.
    affiliate_enabled: bool = False
    amazon_assoc_tag: str = ""  # Amazon Associates tag, e.g. "yourtag-20" (appended to a real product URL)
    affiliate_algoexpert_url: str = ""   # your AlgoExpert referral link
    affiliate_exponent_url: str = ""     # your Exponent referral link
    affiliate_leetcode_url: str = ""     # your LeetCode referral link (if available)
    affiliate_coursera_url: str = ""     # your Coursera affiliate/deep link (usually via Impact)
    affiliate_udemy_url: str = ""        # your Udemy affiliate/deep link
    affiliate_educative_url: str = ""    # your Educative referral link (a full URL)
    affiliate_educative_id: str = ""     # OR just the Educative affiliate ID (built into a URL)
    affiliate_fenzo_url: str = ""        # your Fenzo AI referral link (a full URL)
    affiliate_fenzo_id: str = ""         # OR the Fenzo affiliate ID (Educative's new venture)
    affiliate_max_links: int = Field(4, ge=1, le=10)
    affiliate_in_comment: bool = True    # also include the resources block in the top comment
    # A casual line appended to the resources block (e.g. "some of these get you a discount via my
    # link"). Default empty = off/generic; the operator opts in via AFFILIATE_PERK_TEXT.
    affiliate_perk_text: str = ""
    affiliate_header: str = "\U0001f4da Resources & tools (some are affiliate links):"
    affiliate_disclosure: str = (
        "As an affiliate I may earn a small commission from qualifying purchases through the links "
        "above, at no extra cost to you. Thanks for supporting the channel!"
    )

    # ---------- Proven-idea mining (optional; read-only YouTube Data API v3, no scraping) ----------
    # Surfaces REAL outlier videos (views far above a channel's median) as pre-vetted idea options.
    # Needs only a read-only Data-API key; entirely opt-in (disabled => the picker is unchanged).
    youtube_api_key: str = ""  # public Data-API key (separate from the OAuth publish credentials)
    idea_mining_enabled: bool = False
    # Optional comma list of channel @handles / URLs / UC… ids to mine; blank => search the niche.
    idea_mining_channels: str = ""
    idea_mining_max_channels: int = Field(6, ge=1, le=25)
    idea_mining_videos_per_channel: int = Field(30, ge=5, le=100)
    idea_mining_search_results: int = Field(25, ge=5, le=50)  # topical VIDEO candidates vetted (default)
    idea_mining_outlier_multiple: float = Field(3.0, ge=1.5, le=25.0)  # views >= N x channel median
    idea_mining_max_ideas: int = Field(5, ge=1, le=15)

    # ---------- Notifications ----------
    notify_enabled: bool = True
    notifier: Literal["telegram", "none"] = "telegram"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_events: str = "run_complete,need_validation,video_uploaded,low_credits,run_failed"

    # ---------- Credit / Budget ----------
    monthly_budget_usd: float = 20.0
    low_credit_threshold_pct: float = 80.0
    # Hard cap: abort a run once estimated month-to-date spend reaches the budget (cost safety).
    enforce_budget_cap: bool = True

    # ---------- Storage ----------
    database_url: str = "sqlite:///data/content_foundry.db"
    output_dir: str = "output/runs"

    # ---------- Safeguards ----------
    require_disclosure: bool = True
    require_grounding: bool = True

    # ---------- Content strategy (future plans 3-5) ----------
    time_box_enabled: bool = True
    content_year: int = Field(0, ge=0)  # 0 => current UTC year
    seo_optimize_enabled: bool = True
    seo_max_tags: int = Field(15, ge=0)
    seo_title_max_chars: int = Field(60, ge=10)
    # Hard cap on THUMBNAIL overlay words (scannability at arm's length). The generator aims for 2-4;
    # this trims any overflow so the thumbnail never turns into an unreadable sentence.
    thumbnail_max_words: int = Field(5, ge=2, le=8)
    seo_add_chapters: bool = True
    channel_keywords: str = ""  # comma list of evergreen channel tags (optional)

    # ---------- Ops ----------
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    # Tee EVERY structured log line for a run to output/runs/<id>/run.log (JSON lines) so a run is
    # fully debuggable after the fact — especially SILENT model fallbacks (image 429 -> Pollinations,
    # face-swap/restore skips, etc.). Best-effort; never breaks a run. Set false to skip the file.
    run_log_enabled: bool = True
    schedule_cron: str = "0 9 * * MON"

    # ------------------------------------------------------------------ parsing
    @property
    def enabled_sources_list(self) -> list[str]:
        items = [s.strip().lower() for s in self.enabled_sources.split(",") if s.strip()]
        bad = [s for s in items if s not in VALID_SOURCES]
        if bad:
            raise ConfigError(
                f"Unknown data source(s) in ENABLED_SOURCES: {bad}. "
                f"Valid: {sorted(VALID_SOURCES)}"
            )
        return items

    @property
    def search_facets_list(self) -> list[str]:
        """Facet suffixes for multi-query search fan-out (comma-separated ``SEARCH_FACETS``)."""
        return [f.strip() for f in self.search_facets.split(",") if f.strip()]

    @property
    def google_models_list(self) -> list[str]:
        """Best-first Gemini model ids for the fallback chain (comma-separated ``GOOGLE_MODELS``)."""
        return [m.strip() for m in self.google_models.split(",") if m.strip()]

    @property
    def notify_events_list(self) -> list[str]:
        items = [e.strip() for e in self.notify_events.split(",") if e.strip()]
        bad = [e for e in items if e not in VALID_EVENTS]
        if bad:
            raise ConfigError(f"Unknown NOTIFY_EVENTS: {bad}. Valid: {sorted(VALID_EVENTS)}")
        return items

    @property
    def thumbnail_wh(self) -> tuple[int, int]:
        w, _, h = self.thumbnail_size.partition("x")
        return int(w), int(h)

    @property
    def effective_thumbnail_size(self) -> str:
        """Thumbnail canvas for the format: vertical 9:16 for a Short (so it matches the frame instead
        of being letter-boxed from a 16:9 image), the normal 16:9 thumbnail for long-form."""
        return self.shorts_thumbnail_size if self.is_short else self.thumbnail_size

    @property
    def effective_thumbnail_wh(self) -> tuple[int, int]:
        w, _, h = self.effective_thumbnail_size.partition("x")
        return int(w), int(h)

    @property
    def is_short(self) -> bool:
        """True when producing a vertical YouTube Short (``CONTENT_FORMAT=short``)."""
        return self.content_format == "short"

    @property
    def effective_resolution(self) -> str:
        """Output resolution for the selected content format (vertical for Shorts)."""
        return self.shorts_resolution if self.is_short else self.video_resolution

    @property
    def resolution_wh(self) -> tuple[int, int]:
        w, _, h = self.effective_resolution.partition("x")
        return int(w), int(h)

    @property
    def effective_target_words(self) -> int:
        """Script word target for the format (Shorts are far shorter than long-form)."""
        return self.shorts_target_words if self.is_short else self.script_target_words

    @property
    def effective_scenes(self) -> int:
        """Scene count target for the format."""
        return self.shorts_scenes if self.is_short else self.scenes_per_video

    @property
    def effective_min_scenes(self) -> int:
        """Completeness floor never exceeds the format's own scene count (a short Short is valid)."""
        return min(self.min_scenes, self.effective_scenes)

    @property
    def effective_captions_enabled(self) -> bool:
        """Burn captions? Shorts default ON (watched muted); long-form uses CAPTIONS_ENABLED."""
        return self.shorts_burn_captions if self.is_short else self.captions_enabled

    @property
    def effective_scene_transition(self) -> str:
        """Scene transition for the format (Shorts favour fast hard cuts)."""
        return self.shorts_scene_transition if self.is_short else self.scene_transition

    @property
    def effective_intro_enabled(self) -> bool:
        """Prepend the fixed channel intro tagline? Skipped for Shorts (no time to warm up)."""
        return self.shorts_intro_enabled if self.is_short else self.intro_enabled

    @property
    def effective_avatar_scale(self) -> float:
        """On-frame avatar height as a fraction of the video height. Shorts use HALF the long-form
        scale — the vertical frame is much narrower, so the same fraction looks oversized."""
        return round(self.avatar_scale * 1 / 2, 3) if self.is_short else self.avatar_scale

    @property
    def effective_avatar_position(self) -> str:
        """Corner the avatar sits in. Shorts pin it TOP-RIGHT (the lower third of a vertical frame is
        taken up by captions and the platform UI); long-form keeps the configured corner
        (bottom-right by default)."""
        return "top-right" if self.is_short else self.avatar_position

    @property
    def heavy_model(self) -> str:
        """Model for hard, creative, low-volume work (script generation)."""
        return self.model_heavy or self.generator_model

    @property
    def light_model(self) -> str:
        """Model for mechanical / high-volume work (JSON repair, judge scoring)."""
        return self.model_light or self.judge_model or self.generator_model

    @property
    def effective_content_year(self) -> int:
        """Year used for time-boxing titles; ``content_year`` or the current UTC year."""
        return self.content_year or datetime.now(UTC).year

    @property
    def channel_keywords_list(self) -> list[str]:
        return [k.strip() for k in self.channel_keywords.split(",") if k.strip()]

    @property
    def idea_mining_channels_list(self) -> list[str]:
        """Operator-pinned channels to mine for proven ideas (comma list of @handles/URLs/ids)."""
        return [c.strip() for c in self.idea_mining_channels.split(",") if c.strip()]

    # ------------------------------------------------------------- validation
    @model_validator(mode="after")
    def _validate_cross_fields(self) -> Settings:
        sources = self.enabled_sources_list  # also validates names
        if "adzuna" in sources and not (self.adzuna_app_id and self.adzuna_app_key):
            raise ValueError("adzuna is enabled but ADZUNA_APP_ID/ADZUNA_APP_KEY are not set")

        if "local" in (self.primary_provider, self.fallback_provider) and not self.local_llm_base_url:
            raise ValueError(
                "PRIMARY_PROVIDER/FALLBACK_PROVIDER=local requires LOCAL_LLM_BASE_URL"
            )

        if self.fallback_provider not in ("none", "local"):
            key = {
                "openai": self.openai_api_key,
                "anthropic": self.anthropic_api_key,
                "google": self.google_api_key,
            }.get(self.fallback_provider, "")
            if not key:
                raise ValueError(
                    f"FALLBACK_PROVIDER={self.fallback_provider} requires its API key to be set"
                )

        if self.tts_provider == "elevenlabs" and not self.elevenlabs_api_key:
            raise ValueError("TTS_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY")

        if self.tts_provider == "piper" and not self.piper_model_path:
            raise ValueError("TTS_PROVIDER=piper requires PIPER_MODEL_PATH (path to a .onnx voice)")

        if self.tts_provider == "chatterbox" and not self.tts_reference_clip:
            raise ValueError(
                "TTS_PROVIDER=chatterbox requires TTS_REFERENCE_CLIP (a short WAV of your voice)"
            )

        for _img in (self.image_provider, self.image_fallback_provider):
            if _img == "stability" and not self.stability_api_key:
                raise ValueError("IMAGE_PROVIDER=stability requires STABILITY_API_KEY")
            if _img == "google" and not self.google_api_key:
                raise ValueError("IMAGE_PROVIDER=google requires GOOGLE_API_KEY")

        if self.render_backend == "avatar":
            if self.avatar_provider == "none":
                raise ValueError("RENDER_BACKEND=avatar requires AVATAR_PROVIDER != none")
            if self.avatar_provider == "heygen" and not self.heygen_api_key:
                raise ValueError("AVATAR_PROVIDER=heygen requires HEYGEN_API_KEY")

        if (
            self.publish_mode == "auto"
            and self.youtube_privacy_status == "public"
            and self.require_manual_disclosure_before_public
        ):
            raise ValueError(
                "Refusing auto-public while REQUIRE_MANUAL_DISCLOSURE_BEFORE_PUBLIC=true "
                "(synthetic-content disclosure is non-negotiable)"
            )

        if self.notify_enabled and self.notifier == "telegram" and not (
            self.telegram_bot_token and self.telegram_chat_id
        ):
            raise ValueError(
                "NOTIFY_ENABLED=true with NOTIFIER=telegram requires "
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )

        for _res in (self.video_resolution, self.shorts_resolution):
            parts = (_res or "").lower().split("x")
            if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
                raise ValueError(
                    f"Resolution must be WIDTHxHEIGHT (e.g. 1920x1080 or 1080x1920), got {_res!r}"
                )

        _ = self.notify_events_list  # validate parse
        return self

    # ----------------------------------------------------------- secrets/hash
    @staticmethod
    def _is_secret(name: str) -> bool:
        if name.endswith("_file"):
            return False
        return name.endswith(_SECRET_SUFFIXES)

    def redacted_dict(self) -> dict[str, object]:
        """Config dump with every secret replaced by ``***`` (or empty if unset)."""
        out: dict[str, object] = {}
        for name, value in self.model_dump().items():
            if self._is_secret(name):
                out[name] = "***" if value else ""
            else:
                out[name] = value
        return out

    def credential_status(self) -> dict[str, str]:
        """For ``content-foundry config check``: each secret as ``set ✓`` / ``missing ✗`` (never the value)."""
        return {
            name: ("set ✓" if value else "missing ✗")
            for name, value in self.model_dump().items()
            if self._is_secret(name)
        }

    @property
    def config_hash(self) -> str:
        blob = json.dumps(self.redacted_dict(), sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build (once) and return the shared :class:`Settings`. Fails fast on bad config."""
    try:
        # Resolve the dotenv path at call time so ``ENV_FILE`` can repoint it (and tests stay hermetic).
        return Settings(_env_file=os.environ.get("ENV_FILE", ".env"))
    except ConfigError:
        raise
    except Exception as exc:  # pydantic ValidationError, etc.
        raise ConfigError(f"Invalid configuration: {exc}") from exc


def reset_settings_cache() -> None:
    """Clear the cached settings (used by tests after mutating the environment)."""
    get_settings.cache_clear()
