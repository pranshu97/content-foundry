# 26 — YouTube Shorts Support (vertical short-form)

Status: **implemented**. Adds a first-class **content format** switch so the same pipeline produces
either the standard 16:9 long-form video **or** a vertical 9:16 YouTube **Short**, selected by one
`.env` setting (`CONTENT_FORMAT`) or a per-run `--format` flag.

## 26.1 Goals & non-goals

Goals:
- One switch (`CONTENT_FORMAT=long|short`) flips the whole output shape — resolution, script length,
  pacing, captions, transitions, and publish metadata — with **zero change to long-form behaviour**.
- A Short is a **standalone** ~50 s vertical video generated end-to-end by the existing stages
  (fetch → generate → judge → voiceover → visuals → render → publish). No separate codebase.
- Pull viewers deeper into the channel (subscribe + explore) from the description and, optionally, a
  top comment.

Non-goals (possible later):
- **Deriving** a Short by auto-clipping the best ~50 s out of a rendered long video (segment
  selection + re-timing) — a much larger feature; standalone comes first.
- A talking-head / face-cam format — the pipeline stays **faceless** (b-roll + generated vertical
  backgrounds + big captions).

## 26.2 Design principle: format-aware via `effective_*` properties

The format lives in **config** and is resolved by a small set of read-only properties, so the rest of
the pipeline reads a single `effective_*` value and stays format-agnostic. Long-form uses the
existing fields untouched; Short overrides only the handful of values that differ.

`Settings` (see [06 — environment variables](06-environment-variables-configuration.md)):

| Long-form field | Short override | `effective_*` property |
|---|---|---|
| `video_resolution` (1920x1080) | `shorts_resolution` (1080x1920) | `effective_resolution` / `resolution_wh` |
| `script_target_words` (900) | `shorts_target_words` (150) | `effective_target_words` |
| `scenes_per_video` (10) | `shorts_scenes` (5) | `effective_scenes`, `effective_min_scenes` |
| `captions_enabled` (false) | `shorts_burn_captions` (true) | `effective_captions_enabled` |
| `scene_transition` | `shorts_scene_transition` (none) | `effective_scene_transition` |
| `intro_enabled` (true) | `shorts_intro_enabled` (false) | `effective_intro_enabled` |
| `thumbnail_size` (1280x720) | `shorts_thumbnail_size` (1080x1920) | `effective_thumbnail_size` / `effective_thumbnail_wh` |
| `avatar_position` (bottom-right) | top-right (Shorts) | `effective_avatar_position` |
| `avatar_scale` | 1/2 of long-form (Shorts) | `effective_avatar_scale` |

`is_short` = `content_format == "short"`. `effective_min_scenes = min(min_scenes, effective_scenes)`
so a short Short is never rejected by the completeness floor. Extra Short-only knobs:
`shorts_max_duration_sec` (guard) and `shorts_hashtag` (`#Shorts`).

This keeps the format logic in one place (config), makes every downstream change a one-line swap
(`video_resolution` → `effective_resolution`, etc.), and is trivially unit-testable.

## 26.3 Per-stage changes

- **Script generator (Agent 2, [08](08-agent-2-script-generator.md))** — length/scene targets come
  from `effective_*`. A new `{format_context}` prompt placeholder injects a short-form directive block
  **only** in Short mode (empty for long, so the shipped prompt stays generic): one tight idea, hook
  in the first spoken line, fast punchy sentences, caption-led on-screen text, a quick follow nudge.
  The fixed channel intro tagline is skipped for Shorts (`effective_intro_enabled`).
- **Judge (Agent 3, [09](09-judge-agent.md)) & orchestrator** — the completeness floor (words /
  scenes) uses `effective_*`, so a ~150-word Short passes the same gate a 900-word long-form does.
- **Voiceover (Agent 4)** — unchanged; total length follows the (short) word count.
- **Visuals (Agent 5, [11](11-agent-5-visuals-thumbnail.md))** — scene backgrounds/cards are
  generated at `effective_resolution` (vertical for Shorts). Free image providers that honour a
  WxH (Pollinations) return a true vertical image; any off-ratio source is fixed at render time.
- **Renderer (Agent 6, [12](12-agent-6-video-renderer.md))** — reads `effective_resolution`,
  `effective_captions_enabled`, `effective_scene_transition`. **Aspect-preserving scaling**: the
  ffmpeg filtergraph now scales **to cover** then centre-crops (`scale=…:force_original_aspect_ratio=
  increase, crop=w:h`) instead of stretching to WxH — so 16:9 stock fills a 9:16 frame without
  distortion (this also removes silent stretching of off-ratio clips in long-form). Captions are
  burned for Shorts by default.
- **Publisher / SEO (Agent 7, [13](13-agent-7-youtube-publisher.md))** — for a Short the description
  leads its hashtag line with `#Shorts` and **skips chapters** (they don't apply to a <60 s clip).
  Vertical + <3 min + `#Shorts` is what makes YouTube classify the upload as a Short.

## 26.4 Viewer pull (subscribe + explore)

Requested behaviour: convert a viewer into a subscriber who watches more.
- **Description CTA (both formats)** — `channel_cta_enabled` appends a short "subscribe + explore the
  channel" block (`channel_cta_text` + `youtube_channel_url`) to **every** description. Deterministic,
  always works, no scope needed.
- **Top comment (opt-in, best-effort)** — `publish_top_comment` posts the same CTA as a top-level
  comment via `commentThreads.insert`. This needs the broader **`youtube.force-ssl`** scope, which is
  requested **only** when the flag is on (so upload-only tokens keep working); enabling it means
  re-consenting once (delete the OAuth token — see [Human_Tasks](../Human_Tasks.md)). The Data API
  **cannot pin** a comment — pin it once in Studio. A comment failure never aborts the upload.

## 26.5 B-roll for Shorts — do we need a "1000%" better shortlister?

**No — a rebuild is not the win.** The existing shortlister is already strong (LLM director for
relevance + cross-scene diversity, a hard relevance/denylist gate, one-use-per-clip, page
diversification). The real Short-specific problem was **aspect ratio**: 16:9 stock stretched into a
9:16 frame. That is solved by the render-time **cover + centre-crop** (§26.3). For Shorts the caption
and the generated **vertical background** carry the frame; b-roll is a fast-cut accent. Targeted wins,
in priority order, are: (1) aspect-correct scaling (done), (2) big burned captions (done), (3) faster
cuts / more shots per second (tune `SHORTS_SCENE_TRANSITION`, `broll_director_max_queries`), and later
(4) prefer portrait/square stock or Ken-Burns on the vertical background when a query returns nothing
portrait. Not a new shortlister.

## 26.6 TDD?

Yes — for the **pure, deterministic units** (config `effective_*` properties, `_format_context`
injection, the short length gate, SEO `#Shorts` + channel CTA + skip-chapters, the opt-in comment):
tests were written alongside and lock the behaviour. The **ffmpeg filtergraph** in
`providers/render_backend.py` is coverage-exempt and not unit-tested (the suite uses a fake backend),
so the vertical cover+crop path is validated with a **real-ffmpeg smoke test** (render a 16:9 source
to 1080x1920 and 1920x1080 and probe the output). This mirrors the project's existing test strategy
([22](22-testing-strategy.md)).

## 26.7 Requirements (acceptance criteria)

1. `CONTENT_FORMAT=long|short` in `.env` (default `long`) and `content-foundry run --format short`.
2. Short mode → vertical `1080x1920`, ~150 words, ~5 scenes, big burned captions, no fixed intro,
   hard cuts, `#Shorts` in the description.
3. Long mode is byte-for-byte unchanged (the full existing suite stays green).
4. The short-form prompt block appears only in Short mode and never contains the word "judge"
   (FakeLLM routing safety).
5. Rendering preserves aspect ratio (no stretching) for both formats.
6. The completeness/length gate scales to the Short target so a ~150-word Short PASSes.
7. Every description gets the channel CTA (subscribe + explore); Shorts add `#Shorts` and skip
   chapters; an opt-in best-effort top comment is available.
8. Invalid `SHORTS_RESOLUTION`/`VIDEO_RESOLUTION` fails fast at config load.

## 26.8 How to use

```powershell
# one-off Short (overrides CONTENT_FORMAT for this run)
content-foundry run --niche "ML Career" --idea "Why your resume gets auto-rejected" --format short
# or set CONTENT_FORMAT=short in .env and run normally
```

Tune in `.env`: `SHORTS_TARGET_WORDS`, `SHORTS_SCENES`, `SHORTS_SCENE_TRANSITION`,
`SHORTS_BURN_CAPTIONS`. For the viewer-pull CTA set `YOUTUBE_CHANNEL_URL`; to auto-comment set
`PUBLISH_TOP_COMMENT=true` (then delete the OAuth token to re-consent).

## 26.9 Length range & refinements (bug bash)

- **Ideal length.** A Short retains best at **~35-45 seconds**. Defaults: `SHORTS_TARGET_WORDS=100`,
  `SHORTS_SCENES=4`, `SHORTS_MAX_DURATION_SEC=50`. Enforcement is layered: tighter targets, a HARD
  LENGTH CAP in the short-form prompt block (it overrides the long-form "longer is better" rule), and
  Chatterbox silence-trimming (below). Tune `SHORTS_TARGET_WORDS` down for shorter Shorts.
- **Number pronunciation.** `providers/text_normalize.speechify_numbers()` (num2words) expands
  numerals/currency/`%`/`K·M·B`/`x` to words BEFORE Chatterbox synthesis (so it says "two hundred two
  thousand", not a mangled "202,000"); only the audio is normalized — captions/citations keep digits.
- **Dead-air pauses.** `_trim_silence()` trims Chatterbox's per-chunk leading/trailing silence (with a
  small pad) so stitched sentences/scenes don't accumulate long pauses (also shortens the Short).
- **On-frame avatar.** For Shorts the avatar moves to the **top-right** (`effective_avatar_position`; the
  lower third of a vertical frame is covered by captions + the platform UI) and shrinks to **half** of
  `AVATAR_SCALE` (`effective_avatar_scale`), so a 0.15 long-form avatar becomes 0.075 on a Short.
  Long-form keeps its configured corner and full scale.
- **Vertical thumbnail.** Shorts render a 9:16 thumbnail (`SHORTS_THUMBNAIL_SIZE=1080x1920` via
  `effective_thumbnail_size`/`effective_thumbnail_wh`), matching the frame instead of a letter-boxed
  16:9 image.
- **LLM thumbnail prompt (Thumbnail Director, Agent 5.6).** `THUMBNAIL_DIRECTOR_ENABLED` (default on)
  has an LLM write a rich, per-video image-generation prompt with a hard no-text rule that stops the
  image model baking in gibberish lettering (see [11](11-agent-5-visuals-thumbnail.md)).
- **Thumbnail relevance + regen.** `_faceid_prompt` now leads with a single prominent subject and sets
  the **scene from the script's `thumbnail_concept`**, so the FaceID thumbnail matches the content (at
  a moderate `FACEID_SCALE` ~0.6). Regenerate just the thumbnail — no full re-render — with
  `content-foundry thumbnail --run-id <id> [--face-id/--no-face-id] [--scale N]`.
- **Editable thumbnail prompt.** The exact image prompt used is saved to `assets/thumbnail_prompt.txt`;
  `render_thumbnail` reads it back on the next render, so you can hand-tune the wording and re-run
  `content-foundry thumbnail` for full control. `--prompt "..."` overrides it directly; `--reset`
  rebuilds it from the script's `thumbnail_concept`. (FaceID uses SD1.5, whose CLIP encoder truncates
  beyond ~77 tokens, so the concept leads; the composited `--no-face-id` path takes a longer prompt.)
- **Face into the thumbnail — two methods** (`THUMBNAIL_FACE_METHOD`, when `THUMBNAIL_FACE_ID_ENABLED`):
  **`swap`** (default, recommended) generates a rich scene with the FULL long prompt via the normal
  image provider (no 77-token limit — follows the scene) then swaps your REAL face onto it with
  insightface's `inswapper_128.onnx` (best identity + instruction-following; the model auto-downloads,
  ~530 MB, or set `FACESWAP_MODEL_PATH`). **`generate`** is the older SD1.5 + IP-Adapter-FaceID single
  pass. Both fall back to the composited paste if their models are unavailable.
