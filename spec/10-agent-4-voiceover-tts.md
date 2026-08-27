## 10. Agent 4 — Voiceover / TTS

### 10.1 Purpose
Convert the approved script's narration into a single, clean narration track **with word-level timings**. Those timings drive caption sync (Agent 5) and scene cuts (Agent 6), so this stage is the timing backbone of the whole video.

> **Voice cloning, prosody and tone (current behavior).** Two free, local, zero-shot **voice cloning**
> providers read a short reference clip of your own voice: `TTS_PROVIDER=chatterbox` (MIT) and
> `TTS_PROVIDER=indextts` (IndexTTS-2, Apache-2.0, the current default — emotion is disentangled from
> timbre). IndexTTS-2 runs **in its own venv behind a subprocess worker**, because it requires numpy>=2
> while this package pins numpy<2 for Chatterbox/diffusers; the two can never share an interpreter, so
> `INDEXTTS_PYTHON` points at that venv and the pipeline itself still runs from the main environment.
>
> Long scenes are voiced in chunks and stitched, and three things shape how that sounds:
> - **Pauses follow punctuation, not a fixed pad.** Each chunk's edges are trimmed close
>   (`TTS_EDGE_PAD_MS`) and the gap is then sized by the mark the chunk ends on
>   (`TTS_SENTENCE_PAUSE_MS` for a `.`, ×1.25 for `?`/`!`, ×0.5 for `,`, ×0.25 mid-sentence). Trimming
>   both edges to the same pad at every join is a metronome by construction, which is what it used to be.
> - **The reference clip is condensed first.** Cloning models only listen to their first few seconds, so
>   `TTS_REFERENCE_WINDOW_SEC` selects the densest speech window instead of whatever the clip opens with
>   (0 = use it exactly as recorded).
> - **The window is tone-matched.** `TTS_TONE=auto` picks the window whose measured pace/density/dynamics
>   best fit the script's template (contrarian → punchy, data deep-dive → authoritative, …); pitch median
>   is deliberately *not* optimised, since that is what carries speaker identity.
>
> Any abnormally long INTERNAL pause the cloner emits (longer than `TTS_MAX_PAUSE_MS`, default 1 s) is
> collapsed to the normal beat, so rare 2-3 s dead-air outliers go while every normal pause stays
> byte-identical (0 disables). Cloned voices have even-split word timings (leave burned captions off;
> YouTube auto-CC covers them).

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

**Pronunciation normalisation (`providers/text_normalize.speechify_numbers`, TTS path only — captions keep the digits).** Applied in order, because each pass would otherwise eat the next one's input:
1. Units, then scale suffixes, currency, percentages and ratios (`20ms` → "twenty milliseconds", `$202K` → "two hundred and two thousand dollars").
2. `spell_designations` — letter-and-number tokens: `L5` → "L five", `P99` → "P ninety-nine", `SDE2` → "S D E two", `x86` → "X eighty-six". Must run before the plain-number pass, which would otherwise rewrite only the digits and leave "Lfive" for the voice to guess at.
3. `spell_initialisms` — pure-letter acronyms, which have no digit to anchor on and so fall straight through to the voice: `SLA` was being read as the word "Sla".

**The default is spaced bare capitals** — `SDK` → `S D K`, `API` → `A P I` — not phonetic respellings. Writing the letter *names* out ("ay", "em", "ee") reliably backfires, because a neural voice reads them as words: "ay" is said /aɪ/, so `AI` came out as **"eye eye"**, and the written "em" in "em el ee" was taken as a leading letter of its own, turning a three-letter acronym into four sounds ("E M L E"). A bare capital has no such second reading — every TTS engine already says `M` as "em" — so spacing the letters is both simpler and safer than trying to spell the sound. Plurals are handled the way a person says them: only the final letter takes the `s`, so `APIs` → `A P eyes`, never "A P I S".

Two exception tables sit in front of the default:
- **`_SPOKEN_AS_WORD`** — capital runs that genuinely *are* words, where spelling them would be the bug. `FAANG` is said "fang" and appears in nearly every video on this channel, so "F A A N G" would be unlistenable; `JSON`, `REST`, `CUDA`, `BERT`, `NASA` and friends are exempt for the same reason.
- **`_CUSTOM_SOUNDS`** — hybrids that are part letter and part word, e.g. `VRAM` → "vee ram".

Everything else is spelled, which is the safe default: a spelled initialism is always intelligible, a mis-guessed one is not.

**Why initialisms run last.** Emitting capitals is what forces the ordering: `_SCALED` reads a capital `M`/`K`/`B` after a number as a million/thousand/billion suffix, so running this pass before the number passes would turn "3 MLEs" into "three million L ees". Running it last also keeps a capital run from hiding a unit from `_UNIT` ("16 GB" → "sixteen gigabytes", not "sixteen G B"). Any future pass that emits capitals must be placed the same way.

### 10.3.1 Agent 4.5 — Pronunciation Director (`agents/pronunciation.py`)

The tables above only cover acronyms someone thought of in advance. A script about a new topic will
carry ones nobody curated, and the default (spell it out) is right for `SDK` but wrong for `SQUAD`.
So before voicing, one **LIGHT-tier, temperature-0** call classifies every *uncurated* acronym in the
script as `letters` or `word`, batched into a single request with ~90 characters of surrounding
context each.

The model **only classifies — it never authors the spelling.** A `letters` verdict is rendered by
`spell_letters()`, the same code path as everything else; a `word` verdict passes the acronym through
untouched. Model output is sanitised against `^[a-zA-Z ]{1,40}$` before use, and any failure at all is
swallowed: a pronunciation lookup must never be the reason a voiceover doesn't render. Acronyms
already in `_CUSTOM_SOUNDS` / `_SPOKEN_AS_WORD` are never sent, so a curated decision cannot be
overridden by a model. Set `PRONUNCIATION_LLM_ENABLED=false` to skip the call entirely.

The verdicts are cached per run at `assets/pronunciations.json`, stamped with the
`text_normalize.RULES_VERSION` the spelling rules were at when it was written:

```json
{ "rules_version": 2, "pronunciations": { "SQUAD": "squad" } }
```

**Bump `RULES_VERSION` whenever `spell_letters`, the plural rule, `_CUSTOM_SOUNDS` or
`_SPOKEN_AS_WORD` change.** A cached entry is never re-asked, so without the stamp a re-voice of an
older run would quietly feed yesterday's spellings back in and pass — which is exactly what happened
when run 0030 was re-voiced still holding `{"AI": "ay eye"}` from before the spaced-capitals change.
On a version mismatch (or a legacy unstamped file) the cache is discarded and re-resolved, logging
`pronunciations_discarded`.

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
- **`ChatterboxTTS`** — free zero-shot **voice cloning** (Resemble AI, MIT-licensed ⇒ safe to monetize): clones your voice from one short (~15–30s) reference clip (`TTS_REFERENCE_CLIP`) and runs locally on GPU or CPU (`TTS_CLONE_DEVICE`). GPU is ~5× faster and needs the CUDA torch build (`pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124`); the default pip torch is CPU-only. A single Chatterbox generation caps at ~1000 tokens (~40s), so long scene narration is auto-split into sentence-sized chunks and the audio stitched — otherwise a long scene is truncated mid-sentence and the video cuts away before the line ends. Each chunk's leading/trailing silence is trimmed while keeping a `TTS_SILENCE_PAD_MS` buffer (default 150 ms) so stitched sentences don't accumulate dead air yet transitions stay natural (not abrupt). No native word timings ⇒ even-split per scene, so burned captions drift — leave them off and let YouTube auto-CC caption the audio (see the caption note below).
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
