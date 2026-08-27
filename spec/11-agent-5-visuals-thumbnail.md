## 11. Agent 5 — Visuals & Thumbnail

### 11.1 Purpose
Produce every visual the renderer needs: a click-worthy **thumbnail**, one **visual per scene** (AI-generated image or stock B-roll), and a **time-synced captions** track. Visuals are chosen to reinforce the specific data points, not generic stock fluff.

> **Relevance-first B-roll + generated fallback (current behavior).** Each narration beat gets the single most relevant stock clip (deterministic, no diversity sampling), held to a STRICT on-topic bar — a clip must name a specific (non-generic) word from the beat and, separately, share at least one discriminating word with the exact narration window it will sit under. Any beat with **no confidently-relevant clip** falls back to a bespoke **generated image** whose prompt is written by an LLM art-director (`SceneImageDirector`, gated by `SCENE_IMAGE_DIRECTOR_ENABLED`) that is deliberately composed to AVOID the mangled AI faces/hands look — never an off-topic clip. With no image provider the fallback is a clean designed card.

> **Drawn diagrams instead of photographed ones (`DIAGRAMS_ENABLED`, default on).** Some lines are not
> photographs at all: a cross-company levelling matrix, a comparison of magnitudes, a ranked ladder of
> tiers, a short pipeline. Faking those photographically ("a printed comparison sheet on a desk") costs
> a paid image call AND hands the text to a model that garbles lettering. When the image director marks
> a shot as one of those four shapes it is **DRAWN with matplotlib** instead — free, instant, and the
> labels are exact. The photographic prompt is still written and kept as the fallback if the drawing
> fails, so a diagram shot carries a prompt exactly like a generated one; `shot_prompts.json` records
> `source` per shot so a free chart can be told apart from paid image-model output.

> **Camera motion on stills (`IMAGE_MOTION`, default `auto`).** A generated still sitting frozen between
> moving clips reads as a broken video, so each one gets a slow move matched to its own composition —
> the director's prompt already states the camera it composed for (macro, flat-lay, corridor), so the
> move is chosen deterministically from that text with no vision model. Real stock footage is never
> touched: it already moves, and doubling up looks wrong.

### 11.2 Inputs / outputs
- **Input:** approved `Script` + `VoiceoverAsset` (for caption timing).
- **Output:** `VisualPackage` artifact → `output/runs/<run_id>/visuals.json`, plus `assets/thumbnail.png`, `assets/scenes/scene_<n>.{png|mp4}`, `assets/captions.srt`.

### 11.3 Processing flow
```mermaid
flowchart TD
    A[For each SceneCue] --> B{b_roll_keywords present\nand B-roll enabled?}
    B -->|stock| C[Search per beat -> pick a clip per beat]
    B -->|generated| D[Build image prompt from template\nkeywords + on_screen_text + VISUAL_STYLE]
    D --> D2[ImageProvider.generate]
    C --> E[Record scene visual + source]
    D2 --> E
    E --> F[Build captions.srt from VoiceoverAsset.word_timings]
    F --> G[Compose thumbnail (Pillow): generated image; text ONLY on the fallback card]
    G --> H[Persist VisualPackage + provenance]
```
- **Moment-matched B-roll:** `b_roll_keywords` is an ordered list of short, per-beat search phrases; the agent normalizes each into a stock-searchable query (drops articles/filler), fetches a **separate clip per beat** (one search each), and plays them in sequence so the footage tracks what is being said. Clips come from a **multi-source** pool (Pexels + Pixabay, aggregated by `MultiBrollClient`) chosen by a **run-seeded picker** that biases toward the most relevant (top-ranked) result, de-dups, never repeats a clip back-to-back, caps reuse at 2/video, and still lets different runs pick different clips. Scenes with no stock match fall back to a generated image or a Pillow card.
- **Captions:** generated directly from `word_timings`, grouped into ≤ 7-word cues; written as `captions.srt` (style applied at render time).
- **Drawn charts keep their spec.** When the scene image director judges a shot better DRAWN than photographed it attaches a `diagram` spec, rendered for free by matplotlib with exact lettering. That spec is persisted on `VisualShot.diagram` and into `shot_prompts.json`, and survives a reuse pass. Without it a chart could only be recreated by re-asking the model, which returns DIFFERENT content — so a purely cosmetic layout fix would silently rewrite what the chart *said*. Persisting it makes a redraw free, exact and hand-editable.
- **Thumbnail.** The image ships **exactly as generated**: the old bold text overlay was removed because a good thumbnail already conveys its topic, and text is now drawn only on the Pillow *fallback* card (where the background is an abstract gradient that would otherwise ship looking blank). Sized to `effective_thumbnail_size` (vertical 9:16 for a Short via `SHORTS_THUMBNAIL_SIZE`, else `THUMBNAIL_SIZE`).
  - The prompt is written by the **Thumbnail Director (Agent 5.6, `THUMBNAIL_DIRECTOR_ENABLED`)**, driven by worked EXAMPLES rather than a rulebook — an over-constrained rule list measurably degrades this output. The examples teach a RANGE of moves: stage a real practitioner artifact, let a pure size difference make the argument, or **invert** the thesis and stage the thing the viewer fears (for "Will AI replace ML engineers?", a robot in the engineer's chair wearing their lanyard). The anti-cliche test is **specific vs generic**, NOT concrete vs abstract: a glowing brain could sit on a thousand videos, a robot at a named engineer's desk could only belong to this one — so bold and conceptual is welcome, generic is not.
  - It receives the video's `description`, its spoken `hook` as the **only permitted source of figures** (the description is SEO copy and carries no numbers, so a director working from it alone INVENTS them — a thumbnail promising "5%" over a script that says "10%" contradicts the first line the viewer hears), and `Script.thumbnail_text` as an **offered** headline: the picture should carry the idea, and words are used only if the frame genuinely needs them.
  - **Career-rank words are stripped in code, not merely discouraged.** Image models read "senior", "junior", "veteran", "principal" and "experienced" as AGE rather than rank, so "a senior engineer" renders someone grey-haired in their sixties. `strip_career_rank` removes the adjective in front of a role noun on BOTH the director path and the saved/explicit-prompt path — quoted spans are masked first, so a *drawn label* reading "Senior Signal" survives untouched.
  - The draft is then polished for up to `THUMBNAIL_REFINE_TURNS` rounds of **surgical find/replace edits** (never a rewrite — see 11.5). The exact prompt used is saved to the editable `assets/thumbnail_prompt.txt`. **An explicit or saved prompt BYPASSES the director**, so `content-foundry thumbnail` reuses it verbatim and only `--reset` rebuilds — which means new director rules do not reach an existing run without it.
  - When `THUMBNAIL_USE_AVATAR` is on and `assets/avatar.png` exists, your face is composited in: an opaque source is background-removed with **`rembg`** (cached as `<name>.cutout.png`) and scaled by `THUMBNAIL_AVATAR_SCALE`. Fallback-card text comes from `Script.thumbnail_text` (fallback: first `title_options`, then `thumbnail_concept`) in `script.json` — NOT `visuals.json`, whose copy is only an output record.

### 11.4 `VisualPackage` schema (Pydantic)
```python
class VisualShot(BaseModel):       # one B-roll clip covering a single beat within a scene
    path: str                  # assets/scenes/scene_<n>_shot_<k>.mp4
    duration_sec: float
    source: str                # pexels|pixabay|stock
    query: str                 # the beat's shot description used to find it

class SceneVisual(BaseModel):
    scene_index: int
    kind: Literal["image", "broll"]
    path: str                  # assets/scenes/scene_<n>.{png|mp4} (first beat clip when broll)
    source: str                # openai|stability|pexels|pixabay|card
    prompt_or_query: str
    on_screen_text: str | None # caption / source citation burned on the frame
    sfx: str | None            # sound-effect keyword mixed at this scene's start
    duration_sec: float        # mirrors scene timing
    shots: list[VisualShot]    # ordered per-beat clips (empty for a single image/card)

class VisualPackage(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["visuals"] = "visuals"
    thumbnail_path: str        # assets/thumbnail.png
    thumbnail_text: str
    captions_path: str         # assets/captions.srt
    scenes: list[SceneVisual]
    visual_style: str
    provenance: Provenance
```

### 11.5 Provider abstraction
- **`ImageProvider`** → `OpenAIImage` / `StabilityImage`, selected by `IMAGE_PROVIDER`.
- **B-roll** → `broll.py`: `PexelsBrollClient` (`PEXELS_API_KEY`) + `PixabayBrollClient` (`PIXABAY_API_KEY`) aggregated by `MultiBrollClient` (more variety; resilient if one source is rate-limited); `NullBrollClient` when no key (all scenes generated). A run-seeded picker de-dups, avoids back-to-back repeats, and caps reuse at 2/video.
- **Prompt building is deterministic (no LLM)** for the per-scene images. Per-scene image prompts are assembled by code from a fixed f-string template: `f"{VISUAL_STYLE}; {', '.join(b_roll_keywords)}; on-screen text '{on_screen_text}'; no logos, no real people"`. Scene `kind` is chosen by a simple rule (B-roll when keywords + Pexels available, else generated). The THUMBNAIL prompt is templated from `Script.thumbnail_concept` + overlay text by default, and is optionally upgraded by the LLM Thumbnail Director (11.3) — which still degrades to that same template.

> **Max-savings option:** set `IMAGE_PROVIDER=none` to skip paid image generation too — every scene then uses Pexels B-roll or a Pillow-rendered text/infographic card, for near-zero visual cost.

### 11.6 Resumability hooks
- Each asset is an independent file; the operator can replace a single `scene_<n>.png` or the thumbnail and resume at the renderer.
- `visuals.json` is editable (e.g., swap a scene from `broll` to `image`); re-running only regenerates missing/changed assets.

### 11.7 Failure modes
| Failure | Handling |
|---------|----------|
| Image gen refusal / error | Retry with simplified prompt; then fall back to a neutral B-roll or solid-color card |
| No Pexels match | Fall back to generated image |
| Caption timing gaps | Clamp to scene bounds; never overlap cues |
| Thumbnail text overflow | Auto-shrink font / wrap to fit safe area |

---

---
[← Index](README.md) · [← Prev](10-agent-4-voiceover-tts.md) · [Next →](12-agent-6-video-renderer.md)
