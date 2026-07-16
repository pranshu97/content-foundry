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
    E -->|no| G[Even-split per scene; YouTube auto-CC captions the final audio]
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
    provider: str                   # elevenlabs | edge | piper | openai | chatterbox
    word_timings: list[WordTiming]
    scene_timings: list[SceneTiming]
    provenance: Provenance
```

### 10.5 Provider abstraction
`TTSProvider.synthesize(text) -> (audio_bytes, word_timings | None)`:
- **`ElevenLabsTTS`** (primary) — high quality; returns character/word timestamps natively.
- **`EdgeTTS`** — free Microsoft neural voices (online, no key); returns word timings.
- **`PiperTTS`** — fully offline neural TTS (free; needs a downloaded `.onnx` voice).
- **`OpenAITTS`** (fallback) — no native word timings ⇒ even-split per scene (burned captions off by default; see the caption note below).
- **`ChatterboxTTS`** — free zero-shot **voice cloning** (Resemble AI, MIT-licensed ⇒ safe to monetize): clones your voice from one short (~15–30s) reference clip (`TTS_REFERENCE_CLIP`) and runs locally on GPU or CPU (`TTS_CLONE_DEVICE`). GPU is ~5× faster and needs the CUDA torch build (`pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124`); the default pip torch is CPU-only. A single Chatterbox generation caps at ~1000 tokens (~40s), so long scene narration is auto-split into sentence-sized chunks and the audio stitched — otherwise a long scene is truncated mid-sentence and the video cuts away before the line ends. No native word timings ⇒ even-split per scene, so burned captions drift — leave them off and let YouTube auto-CC caption the audio (see the caption note below).
Voice, model, and format come from `TTS_VOICE_ID` / `TTS_MODEL` / `TTS_FORMAT`.

> **Captions (narration):** burned-in subtitles are **off by default** (`CAPTIONS_ENABLED=false`). Only ElevenLabs and Edge emit real word timings; Chatterbox/Piper/OpenAI even-split, which drifts — so YouTube's free auto-generated CC (run on the real final audio) is the default path. Enable burned narration captions only with a timing-capable voice. The on-screen **source citations** are a separate track and are always burned in (they aren't spoken, so YouTube CC can't reproduce them).

**Voice by run-id parity:** `pick_voice(run_id, ...)` in `providers/tts.py` alternates the narrator so consecutive videos don't sound identical — the **male** voice (`TTS_VOICE_MALE`) for odd run ids, the **female** voice (`TTS_VOICE_FEMALE`) for even. Both blank ⇒ always use `TTS_VOICE_ID`. The chosen voice is recorded in `VoiceoverAsset.voice_id`.

### 10.6 Resumability hooks
- The operator can swap the voice or hand-edit `voiceover.json` (e.g., trim a pause) and resume at Agent 5.
- If `assets/narration.mp3` already exists and is unchanged (hash match), re-runs are skipped unless `--force`.

### 10.7 Failure modes
| Failure | Handling |
|---------|----------|
| TTS provider error / rate limit | `tenacity` retry, then fall back to secondary provider |
| Chunk stitch gap drift | Scene/word timings are locked to the **decoded** audio length — each chunk is measured and re-encoded into one gapless MP3, so the visuals never drift ahead of the voice |
| TTS reports no word timings | Even-split per scene (Chatterbox/Piper/OpenAI); burned captions drift, so they're off by default — YouTube auto-CC captions the final audio |

---

---
[← Index](README.md) · [← Prev](09-judge-agent.md) · [Next →](11-agent-5-visuals-thumbnail.md)
