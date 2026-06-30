## 12. Agent 6 — Video Renderer

### 12.1 Purpose
Assemble the narration, per-scene visuals, and captions into a single, upload-ready `.mp4`. The renderer is **purely deterministic** — it interprets nothing; it just executes the timeline implied by the scene timings.

### 12.2 Inputs / outputs
- **Input:** `VoiceoverAsset` (audio + timings) + `VisualPackage` (visuals + captions).
- **Output:** `VideoAsset` artifact → `output/runs/<run_id>/video.json`, plus the final `assets/video.mp4`.

### 12.3 Processing flow
```mermaid
flowchart TD
    A[Build timeline from scene_timings] --> B[Per scene: place visual for its duration]
    B --> C[Stills: Ken Burns zoom/pan; B-roll: trim/loop to fit]
    C --> D[Burn-in captions.srt (styled)]
    D --> E[Optional intro/outro cards]
    E --> F[Mux narration.mp3 as audio track]
    F --> G[Encode to VIDEO_RESOLUTION @ VIDEO_FPS (H.264/AAC)]
    G --> H[Persist VideoAsset + provenance]
```
- **Timeline:** scene durations come straight from `VoiceoverAsset.scene_timings`, so audio and visuals stay locked.
- **Motion:** still images get a subtle Ken Burns effect; B-roll is trimmed or gently looped to its scene length.
- **Captions:** `captions.srt` is burned in with a readable style (configurable font/size/position).
- **Audio:** the single narration track is muxed; optional bed music can be mixed at low gain (future toggle).

### 12.4 Render-backend abstraction
`RenderBackend.render(timeline, assets, config) -> mp4_path`, selected by `RENDER_BACKEND`:
- **`FfmpegBackend`** (default) — builds an `ffmpeg` filtergraph via `ffmpeg-python`; fast, faceless slideshow + B-roll + captions.
- **`MoviePyBackend`** (optional) — easier transitions/effects, heavier runtime.
- **`AvatarBackend`** (optional) — delegates to a talking-head provider (`AVATAR_PROVIDER`=heygen/did): sends narration + script, retrieves a rendered avatar video, then overlays captions/branding.

### 12.5 `VideoAsset` schema (Pydantic)
```python
class VideoAsset(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["video"] = "video"
    video_path: str            # assets/video.mp4
    duration_sec: float
    resolution: str            # e.g. "1920x1080"
    fps: int
    backend: str               # ffmpeg|moviepy|avatar
    has_captions: bool
    file_size_bytes: int
    provenance: Provenance
```

### 12.6 Resumability hooks
- Rendering is the most expensive step; it is skipped if `video.mp4` exists and inputs are unchanged (hash check) unless `--force`.
- The operator can drop in a fully external `video.mp4` (matching `VideoAsset`) and resume at the publisher.

### 12.7 Failure modes
| Failure | Handling |
|---------|----------|
| `ffmpeg` not found | Fail fast with install instructions ([Ch. 23](23-deployment-instructions.md#23-deployment-instructions)) |
| Asset duration mismatch | Pad/trim visual to scene bounds; warn |
| Encode failure | Retry once at safer preset; then fail render |
| Avatar provider timeout | Fall back to `FfmpegBackend` if `RENDER_FALLBACK=true` |

---

---
[← Index](README.md) · [← Prev](11-agent-5-visuals-thumbnail.md) · [Next →](13-agent-7-youtube-publisher.md)
