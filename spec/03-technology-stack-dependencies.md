## 3. Technology Stack & Dependencies

### 3.1 Language & runtime
- **Python 3.11+** (required for modern typing + `tomllib`). Single-language codebase keeps the agent/LLM ecosystem first-class.

### 3.2 Core libraries
| Concern | Library | Why |
|---------|---------|-----|
| LLM — Anthropic | `anthropic` | Primary model provider (Claude) |
| LLM — OpenAI | `openai` | Fallback provider + optional judge cross-check |
| Data validation / schemas | `pydantic>=2` | All artifacts are Pydantic models ⇒ free JSON (de)serialization + validation |
| Settings / config | `pydantic-settings` | Typed env-var loading from `.env` |
| HTTP client | `httpx` | Async-capable, modern, used by all data fetchers |
| HTML parsing (scrape fallback) | `beautifulsoup4` + `lxml` | For sources without a clean API |
| Database ORM | `SQLAlchemy>=2` | Run/artifact metadata persistence |
| DB engine (default) | `sqlite3` (stdlib) | Zero-config, file-based, single-operator friendly |
| CLI framework | `typer` | Declarative, typed CLI with great UX |
| Scheduling | `APScheduler` | In-process cron-like scheduling |
| Dashboard | `streamlit` | Fastest path to a read-mostly review UI |
| Retry/backoff | `tenacity` | Resilient network + LLM calls |
| Structured logging | `structlog` | JSON logs, run-scoped context |
| Rich CLI output | `rich` | Tables, progress, readable reports in terminal |
| Date/time | stdlib `datetime` + `python-dateutil` | Parsing feed timestamps |
| IDs | `python-ulid` | Sortable `attempt_id`/`artifact_id` (the `run_id` is a sequential 4-digit number) |
| TTS narration | `elevenlabs`, `openai`, `edge-tts` (free), `piper-tts` (free offline) | Voiceover with word-level timings |
| Voice cloning (TTS) | `chatterbox-tts` + `torch`/`torchaudio` | Free zero-shot cloning of your own voice (MIT); GPU via the CUDA `cu124` torch build |
| Thumbnail face cutout | `rembg` | Background-remove your avatar PNG for the thumbnail composite |
| Image generation | `openai` Images / `stability-sdk` | Thumbnail + per-scene visuals |
| Stock B-roll | Pexels + Pixabay APIs via `httpx` | Free background footage (multi-source, aggregated for variety) |
| Web search (data source) | `ddgs` (DuckDuckGo) + Tavily/Brave via `httpx` | Domain-agnostic topic research |
| Sound effects mixing | `pydub` | Overlay SFX clips onto the narration |
| Image/text overlay | `Pillow` | Thumbnail composition |
| Video assembly | `ffmpeg` via `ffmpeg-python` (primary), `moviepy` (optional) | Render slideshow/B-roll + captions |
| YouTube upload | `google-api-python-client`, `google-auth-oauthlib` | OAuth + Data API v3 `videos.insert` |

### 3.3 Dev / test tooling
| Concern | Library |
|---------|---------|
| Test runner | `pytest` |
| Mocking | `pytest-mock` |
| HTTP mocking | `respx` (httpx-native) |
| Coverage | `pytest-cov` |
| Formatting | `ruff format` (or `black`) |
| Linting | `ruff` |
| Type checking | `mypy` |
| Pre-commit hooks | `pre-commit` |

### 3.4 Provider abstraction (no vendor lock-in)
All model access goes through an internal `LLMProvider` protocol with two concrete implementations (`AnthropicProvider`, `OpenAIProvider`) and a `FallbackProvider` that tries primary then secondary. Agents depend on the protocol, **never** on a vendor SDK directly. Swapping models is a config change, not a code change.

### 3.5 Data-source abstraction
All external data access goes through a `DataSource` protocol (`fetch() -> list[RawSignal]`). Concrete sources are pluggable and individually toggleable via config:
- **Job postings & salary:** Adzuna API (free tier, structured salary + posting volume).
- **Layoffs:** `layoffs.fyi`-style RSS / public dataset, or a configurable RSS endpoint.
- **Industry reports / news:** NewsAPI (or RSS fallback) filtered to labor-market keywords.
- **Government baseline (optional):** U.S. BLS public data series for occupation outlook.
- **Web search (domain-agnostic — the default source):** a general web-search source that queries the run's topic (niche + idea) directly, so **any** niche works, not just the labor-market feeds. Free via DuckDuckGo (no key); optional Tavily/Brave keys for a stronger index. It is the default in `ENABLED_SOURCES`; the adzuna/layoffs/news/bls feeds are career-specific opt-ins.

> Any source can be disabled; the pipeline tolerates missing sources and notes coverage gaps in the `DataBrief`. Valid sources: `adzuna | layoffs | news | bls | search`.

### 3.5a Media & publishing abstractions
Production agents depend only on these protocols, never on a concrete vendor:
- **`TTSProvider`** → `ElevenLabsTTS`, `OpenAITTS`, `EdgeTTS` (free), `PiperTTS` (free offline), `ChatterboxTTS` (free zero-shot **voice cloning**, MIT; local CPU/CUDA). Returns audio bytes + optional word timings.
- **`ImageProvider`** → `OpenAIImage` / `StabilityImage` for thumbnail + scene art.
- **`RenderBackend`** → `FfmpegBackend` (default faceless slideshow + B-roll + captions, plus scene crossfades, a warm colour grade, SFX mixing and a subscribe-nudge badge), with optional `MoviePyBackend` and `AvatarBackend` (HeyGen/D-ID), selected via `RENDER_BACKEND`.
- **`BrollClient`** → `PexelsBrollClient` + `PixabayBrollClient` aggregated by `MultiBrollClient` (free stock video; more variety across videos); `NullBrollClient` when no key is set.
- **`SearchProvider`** → `DuckDuckGoProvider` (default, no key; the `ddgs` library with a DuckDuckGo Instant-Answer API fallback), `TavilyProvider`, `BraveProvider`, selected via `SEARCH_PROVIDER`.
- **`SfxClient`** → `SfxLibrary` (local `data/sounds` match + optional Freesound download) or `NullSfxClient`, gated by `SFX_ENABLED`.
- **`Publisher`** → `YouTubePublisher` (OAuth, privacy-gated upload, disclosure flag).
All are swappable via config; nothing downstream depends on a concrete vendor.

> **System dependency:** `ffmpeg` must be installed and on `PATH` (see [Chapter 23](23-deployment-instructions.md#23-deployment-instructions)).

### 3.6 `requirements.txt` (authoritative)
```txt
anthropic>=0.39
openai>=1.50
pydantic>=2.7
pydantic-settings>=2.3
httpx>=0.27
beautifulsoup4>=4.12
lxml>=5.2
SQLAlchemy>=2.0
typer>=0.12
APScheduler>=3.10
streamlit>=1.37
tenacity>=8.5
structlog>=24.1
rich>=13.7
python-dateutil>=2.9
python-ulid>=2.7
ddgs>=6.0
# media / production
elevenlabs>=1.5
edge-tts>=6.1
piper-tts>=1.2
chatterbox-tts>=0.1   # optional (extra: clone) - free voice cloning; also needs torch+torchaudio (CUDA cu124 build for GPU)
pydub>=0.25
stability-sdk>=0.8
Pillow>=10.4
rembg>=2.0            # optional (extra: avatar) - cut the avatar background for the thumbnail
ffmpeg-python>=0.2
moviepy>=1.0.3
# publishing
google-api-python-client>=2.140
google-auth-oauthlib>=1.2
google-auth-httplib2>=0.2
# dev
pytest>=8.3
pytest-mock>=3.14
pytest-cov>=5.0
respx>=0.21
ruff>=0.6
mypy>=1.11
pre-commit>=3.8
```

### 3.7 External accounts / keys required
- Anthropic API key (primary LLM).
- OpenAI API key (fallback LLM + TTS/image fallback — recommended).
- Adzuna `app_id` + `app_key` (free tier).
- NewsAPI key (optional).
- ElevenLabs API key (TTS narration; optional if using OpenAI TTS).
- Pexels API key (free stock B-roll; optional).
- Stability API key (optional image backend).
- Google OAuth client secrets (`client_secrets.json`) for the YouTube Data API v3.
- YouTube Data API v3 **key** (read-only) — optional, for proven-idea mining (`YOUTUBE_API_KEY`; separate from the OAuth publish creds).
All keys are supplied via environment variables (see [Chapter 6](06-environment-variables-configuration.md#6-environment-variables--configuration)); none are hard-coded.

---

---
[← Index](README.md) · [← Prev](02-system-architecture.md) · [Next →](04-full-file-directory-structure.md)
