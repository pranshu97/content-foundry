## 11. Agent 5 — Visuals & Thumbnail

### 11.1 Purpose
Produce every visual the renderer needs: a click-worthy **thumbnail**, one **visual per scene** (AI-generated image or stock B-roll), and a **time-synced captions** track. Visuals are chosen to reinforce the specific data points, not generic stock fluff.

### 11.2 Inputs / outputs
- **Input:** approved `Script` + `VoiceoverAsset` (for caption timing).
- **Output:** `VisualPackage` artifact → `output/runs/<run_id>/visuals.json`, plus `assets/thumbnail.png`, `assets/scenes/scene_<n>.{png|mp4}`, `assets/captions.srt`.

### 11.3 Processing flow
```mermaid
flowchart TD
    A[For each SceneCue] --> B{b_roll_keywords present\nand Pexels enabled?}
    B -->|stock| C[Pexels search -> pick clip]
    B -->|generated| D[Build image prompt from template\nkeywords + on_screen_text + VISUAL_STYLE]
    D --> D2[ImageProvider.generate]
    C --> E[Record scene visual + source]
    D2 --> E
    E --> F[Build captions.srt from VoiceoverAsset.word_timings]
    F --> G[Compose thumbnail (Pillow): base image + overlay text]
    G --> H[Persist VisualPackage + provenance]
```
- **Visual choice per scene:** data/number-heavy scenes prefer **generated infographic-style images** (driven by `VISUAL_STYLE`); narrative/illustrative scenes prefer **B-roll** from Pexels. Falls back to generation if no good stock match.
- **Captions:** generated directly from `word_timings`, grouped into ≤ 7-word cues; written as `captions.srt` (style applied at render time).
- **Thumbnail:** base image (generated from `Script.thumbnail_concept`) + bold overlay text via `Pillow`, sized to `THUMBNAIL_SIZE`.

### 11.4 `VisualPackage` schema (Pydantic)
```python
class SceneVisual(BaseModel):
    scene_index: int
    kind: Literal["image", "broll"]
    path: str                  # assets/scenes/scene_<n>.{png|mp4}
    source: str                # openai|stability|pexels
    prompt_or_query: str
    duration_sec: float        # mirrors scene timing

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
- **B-roll** → `broll.py` Pexels client (`PEXELS_API_KEY`); disabled gracefully if no key (all scenes generated).
- **Prompt building is deterministic (no LLM).** Per-scene image prompts are assembled by code from a fixed f-string template: `f"{VISUAL_STYLE}; {', '.join(b_roll_keywords)}; on-screen text '{on_screen_text}'; no logos, no real people"`. Scene `kind` is chosen by a simple rule (B-roll when keywords + Pexels available, else generated). The thumbnail prompt is templated from `Script.thumbnail_concept` + overlay text. This removes the dedicated LLM pass entirely.

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
