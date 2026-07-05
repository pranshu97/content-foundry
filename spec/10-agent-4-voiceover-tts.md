## 10. Agent 4 — Voiceover / TTS

### 10.1 Purpose
Convert the approved script's narration into a single, clean narration track **with word-level timings**. Those timings drive caption sync (Agent 5) and scene cuts (Agent 6), so this stage is the timing backbone of the whole video.

### 10.2 Inputs / outputs
- **Input:** approved `Script` artifact.
- **Output:** `VoiceoverAsset` artifact → `output/runs/<run_id>/voiceover.json`, plus audio at `assets/narration.mp3`.

### 10.3 Processing flow
```mermaid
flowchart TD
    A[Concatenate scene narration in order] --> B[Chunk by provider char limit]
    B --> C[TTSProvider.synthesize() per chunk]
    C --> D[Stitch audio -> narration.mp3]
    D --> E{Word timings returned?}
    E -->|yes| F[Use provider timings]
    E -->|no| G[CAPTION_ALIGNER=whisper -> faster-whisper align]
    F --> H[Compute per-scene start/end]
    G --> H
    H --> I[Persist VoiceoverAsset + provenance]
```
- Narration is taken **verbatim** from `Script.scenes[*].narration` (the Judge already approved these words).
- Long scripts are chunked to respect provider limits, then stitched with short, even gaps so timings stay monotonic.
- Per-scene `start`/`end` offsets are derived from word timings and stored for downstream alignment.

### 10.4 `VoiceoverAsset` schema (Pydantic)
```python
class WordTiming(BaseModel):
    word: str
    start: float    # seconds
    end: float

class SceneTiming(BaseModel):
    scene_index: int
    start: float
    end: float

class VoiceoverAsset(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["voiceover"] = "voiceover"
    audio_path: str                 # assets/narration.mp3
    duration_sec: float
    sample_rate: int
    voice_id: str
    provider: str                   # elevenlabs | edge | piper | openai
    word_timings: list[WordTiming]
    scene_timings: list[SceneTiming]
    provenance: Provenance
```

### 10.5 Provider abstraction
`TTSProvider.synthesize(text) -> (audio_bytes, word_timings | None)`:
- **`ElevenLabsTTS`** (primary) — high quality; returns character/word timestamps natively.
- **`EdgeTTS`** — free Microsoft neural voices (online, no key); returns word timings.
- **`PiperTTS`** — fully offline neural TTS (free; needs a downloaded `.onnx` voice).
- **`OpenAITTS`** (fallback) — no native word timings ⇒ alignment falls back to `faster-whisper`.
Voice, model, and format come from `TTS_VOICE_ID` / `TTS_MODEL` / `TTS_FORMAT`.

**Voice by run-id parity:** `pick_voice(run_id, ...)` in `providers/tts.py` alternates the narrator so consecutive videos don't sound identical — the **male** voice (`TTS_VOICE_MALE`) for odd run ids, the **female** voice (`TTS_VOICE_FEMALE`) for even. Both blank ⇒ always use `TTS_VOICE_ID`. The chosen voice is recorded in `VoiceoverAsset.voice_id`.

### 10.6 Resumability hooks
- The operator can swap the voice or hand-edit `voiceover.json` (e.g., trim a pause) and resume at Agent 5.
- If `assets/narration.mp3` already exists and is unchanged (hash match), re-runs are skipped unless `--force`.

### 10.7 Failure modes
| Failure | Handling |
|---------|----------|
| TTS provider error / rate limit | `tenacity` retry, then fall back to secondary provider |
| Chunk stitch gap drift | Normalize timings to measured audio duration |
| Alignment unavailable | Even-split timings per scene as last resort (logged, lower caption precision) |

---

---
[← Index](README.md) · [← Prev](09-judge-agent.md) · [Next →](11-agent-5-visuals-thumbnail.md)
