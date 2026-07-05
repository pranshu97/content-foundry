## 12. Agent 6 — Video Renderer

### 12.1 Purpose
Assemble the narration, per-scene visuals, and captions into a single, upload-ready `.mp4`. The renderer is **purely deterministic** — it interprets nothing; it just executes the timeline implied by the scene timings.

### 12.2 Inputs / outputs
- **Input:** `VoiceoverAsset` (audio + timings) + `VisualPackage` (visuals + captions).
- **Output:** `VideoAsset` artifact → `output/runs/<run_id>/video.json`, plus the final `assets/video.mp4`.

### 12.3 Processing flow
```mermaid
flowchart TD
    A[Build timeline from scene_timings] --> B[Per scene: concat its per-beat clips]
    B --> C[Crossfade between scenes (SCENE_TRANSITION) + warm grade (COLOR_WARMTH)]
    C --> D[Burn-in captions.srt + top-pinned Source citations]
    D --> E[Overlay avatar + midpoint Subscribe nudge]
    E --> F[Mix SFX cues onto narration; mux audio (speed via VIDEO_SPEED)]
    F --> G[Encode to VIDEO_RESOLUTION @ VIDEO_FPS (H.264/AAC)]
    G --> H[Persist VideoAsset + provenance]
```
- **Timeline:** scene durations come straight from `VoiceoverAsset.scene_timings`, so audio and visuals stay locked. Each scene is assembled from its ordered **per-beat clips** (`RenderSegment.clips`) concatenated to fill the scene, then scenes are joined.
- **Transitions & grade:** consecutive scenes cross-blend via ffmpeg `xfade` when `SCENE_TRANSITION != none` (`SCENE_TRANSITION_SEC`); a warm colour grade is applied when `COLOR_WARMTH > 0`.
- **Captions & citations:** `captions.srt` (word-timed) is burned in with a readable style; a separate top-pinned track burns the on-screen **source citations** (`Source: Adzuna`) for each scene's stat.
- **Sound effects:** when `SFX_ENABLED`, the script's `sfx` cues are mixed onto the narration at each scene's start (`production/sound_design.py::mix_sfx` → `assets/narration_mixed.mp3`); the Subscribe bell (when enabled) is mixed in as one extra cue at the badge's fade-in time.
- **Branding:** an optional avatar image is composited in a corner (`AVATAR_OVERLAY_ENABLED`), and a small **Subscribe** badge (a bell + label) fades in at the video's midpoint (`SUBSCRIBE_NUDGE_ENABLED`, `production/subscribe.py`); when `SUBSCRIBE_BELL_ENABLED` and SFX are on, a bell chime (`SUBSCRIBE_BELL_SOUND` → a clip in `sfx_dir`) rings exactly as it appears.
- **Speed:** the whole video can be sped up/slowed via `VIDEO_SPEED` (audio pitch preserved; captions stay in sync).
- **Audio:** the single narration track (SFX-mixed if enabled) is muxed to H.264/AAC.

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
    has_avatar: bool           # true if an avatar overlay was composited
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
