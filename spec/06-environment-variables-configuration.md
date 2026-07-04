## 6. Environment Variables & Configuration

Configuration is loaded by a single typed `Settings` object (`config.py`, built on `pydantic-settings`). Values come from environment variables / `.env`. **No secret is ever hard-coded.** Missing required keys fail fast at startup with a clear message.

> **One place for every credential.** All API keys, bot tokens, and passwords live in exactly **one file** — `.env` (loaded automatically). Nothing else in the codebase reads secrets from anywhere else. To rotate or add a credential, edit that single file; run `content-foundry config check` to validate it (§6.6).

### 6.1 `.env.example` (authoritative template)
```dotenv
# ---------- LLM Providers ----------
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx          # required (primary)
OPENAI_API_KEY=sk-xxxxxxxx                 # optional (fallback / judge cross-check)
PRIMARY_PROVIDER=anthropic                 # anthropic | openai
FALLBACK_PROVIDER=openai                   # anthropic | openai | none
GENERATOR_MODEL=claude-sonnet-4-20250514   # model for Agent 2 (the one always-on LLM)
JUDGE_MODEL=claude-sonnet-4-20250514       # model for the Judge (used only in hybrid/llm mode)
# FETCHER_MODEL removed - Agent 1 distillation is now deterministic (no LLM)
LLM_TEMPERATURE=0.7                        # generator creativity
JUDGE_TEMPERATURE=0.0                      # judge determinism
LLM_MAX_TOKENS=4096

# ---------- Data Sources ----------
ADZUNA_APP_ID=xxxx                         # required if adzuna enabled
ADZUNA_APP_KEY=xxxx
NEWSAPI_KEY=xxxx                           # optional
ENABLED_SOURCES=adzuna,layoffs,news        # comma list: adzuna|layoffs|news|bls
LAYOFFS_FEED_URL=https://example.com/layoffs.rss
SIGNAL_CACHE_TTL_MIN=720                   # reuse cached signals within 12h

# ---------- Pipeline Behavior ----------
MAX_REVISIONS=3                            # generator<->judge loop bound
JUDGE_MODE=hybrid                          # hybrid | deterministic | llm  (cost control)
PASS_THRESHOLD=7.5                         # min weighted rubric total to PASS
INSIGHT_MIN=7.0                            # hard floor on Insight Score
GROUNDING_MIN=8.0                          # hard floor on factual grounding
FATIGUE_LOOKBACK=5                         # # of recent runs checked for template repetition
TARGET_NICHE=tech careers                  # default content domain
BRAINSTORM_ENABLED=true                     # Agent 0 proposes a fresh idea each run (fixes topic collapse)
BRAINSTORM_IDEA_COUNT=5                      # ideas proposed per run (interactive pick on a TTY, else first)
REQUIRE_SCRIPT_APPROVAL=false               # true = pause after a PASS for sign-off, then resume
SCRIPT_TARGET_WORDS=900                    # ~6-7 min video
MIN_FACTS=3                                # min grounded facts Agent 1 must produce (else the run fails)
MIN_SCENES=3                               # completeness gate: reject scripts with fewer scenes
MIN_SCRIPT_WORD_RATIO=0.5                  # completeness gate: reject drafts < this x SCRIPT_TARGET_WORDS
GATE_RELIEF_SCORE=9.0                      # drafts scoring >= this get slack on insight+length floors (>10 disables)
GATE_RELIEF_RATIO=0.20                     # slack amount (20%); never grounding/compliance/fatigue

# ---------- Voiceover (TTS) ----------
TTS_PROVIDER=elevenlabs                     # elevenlabs | openai
ELEVENLABS_API_KEY=xxxx                     # required if TTS_PROVIDER=elevenlabs
TTS_VOICE_ID=Rachel                         # provider voice id / name
TTS_MODEL=eleven_multilingual_v2
TTS_FORMAT=mp3_44100_128

# ---------- Visuals ----------
IMAGE_PROVIDER=openai                        # openai | stability | none (none = B-roll/Pillow cards only)
STABILITY_API_KEY=xxxx                       # required if IMAGE_PROVIDER=stability
PEXELS_API_KEY=xxxx                          # optional B-roll
VISUAL_STYLE=clean infographic, high-contrast, bold text
SCENES_PER_VIDEO=10                          # target number of distinct visuals
THUMBNAIL_SIZE=1280x720

# ---------- Render ----------
RENDER_BACKEND=ffmpeg                         # ffmpeg | moviepy | avatar
AVATAR_PROVIDER=none                          # none | heygen | did
HEYGEN_API_KEY=xxxx                           # required if RENDER_BACKEND=avatar & AVATAR_PROVIDER=heygen
VIDEO_RESOLUTION=1920x1080
VIDEO_FPS=30
CAPTIONS_ENABLED=true
CAPTION_ALIGNER=tts                           # tts | whisper (fallback alignment)

# ---------- Publishing (YouTube) ----------
YOUTUBE_CLIENT_SECRETS_FILE=secrets/client_secrets.json
YOUTUBE_TOKEN_FILE=secrets/youtube_token.json
PUBLISH_MODE=draft                            # draft | auto  (draft = upload then stop for approval)
YOUTUBE_PRIVACY_STATUS=private                # private | unlisted | public
YOUTUBE_CATEGORY_ID=22                        # 22 = People & Blogs
YOUTUBE_DEFAULT_LANGUAGE=en
REQUIRE_MANUAL_DISCLOSURE_BEFORE_PUBLIC=true  # hard gate: never auto-publish public without disclosure

# ---------- Notifications (free Telegram bot) ----------
NOTIFY_ENABLED=true
NOTIFIER=telegram                          # telegram | none
TELEGRAM_BOT_TOKEN=xxxx                    # from @BotFather (free)
TELEGRAM_CHAT_ID=xxxx                      # your chat / user id
NOTIFY_EVENTS=run_complete,need_validation,video_uploaded,low_credits,run_failed

# ---------- Credit / Budget Monitoring ----------
MONTHLY_BUDGET_USD=20                       # alert when projected monthly spend exceeds this
LOW_CREDIT_THRESHOLD_PCT=80                 # fire low_credits alert at this % of budget

# ---------- Storage ----------
DATABASE_URL=sqlite:///data/content_foundry.db
OUTPUT_DIR=output/runs

# ---------- Safeguards / Compliance ----------
REQUIRE_DISCLOSURE=true                    # force synthetic-content disclosure block
REQUIRE_GROUNDING=true                     # reject ungrounded statistical claims

# ---------- Ops ----------
LOG_LEVEL=INFO                             # DEBUG|INFO|WARNING|ERROR
LOG_FORMAT=json                            # json|console
SCHEDULE_CRON=0 9 * * MON                  # weekly Monday 09:00 (used by scheduler)
```

### 6.2 `Settings` model (shape)
`config.py` defines a `Settings(BaseSettings)` with typed, validated fields grouped to mirror the sections above. Highlights:
- **Enums** for `PRIMARY_PROVIDER`, `FALLBACK_PROVIDER`, `LOG_FORMAT`.
- **`ENABLED_SOURCES`** parsed into `list[SourceName]`; validator rejects unknown names.
- **Cross-field validators:** if `adzuna` is enabled, `ADZUNA_APP_ID`/`KEY` must be present; if `FALLBACK_PROVIDER != none`, its key must exist.
- **Threshold bounds:** `PASS_THRESHOLD`, `INSIGHT_MIN`, `GROUNDING_MIN` constrained to `0–10`.
- A module-level `settings = Settings()` singleton; everything imports from it. A `config_hash` property (sha256 of the resolved, secret-redacted config) is written into every artifact's provenance for reproducibility.
- **Media/publishing validators:** if `TTS_PROVIDER=elevenlabs`, `ELEVENLABS_API_KEY` is required; if `IMAGE_PROVIDER=stability`, `STABILITY_API_KEY` is required; if `RENDER_BACKEND=avatar`, `AVATAR_PROVIDER` must be ≠`none` with its key set; if `PUBLISH_MODE=auto` and `YOUTUBE_PRIVACY_STATUS=public`, then `REQUIRE_MANUAL_DISCLOSURE_BEFORE_PUBLIC` **must** be `false` — otherwise startup fails (disclosure is non-negotiable).
- **Notification validator:** if `NOTIFY_ENABLED=true` and `NOTIFIER=telegram`, both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are required.

### 6.3 Configuration precedence
1. Explicit CLI flags (highest) — e.g., `--max-revisions`, `--template`, `--niche`.
2. Environment variables / `.env`.
3. Built-in defaults in `Settings`.

### 6.4 Secrets handling
- `.env` is gitignored; only `.env.example` is committed.
- Secrets are **redacted** in logs and in any `provenance`/`config_hash` output (keys replaced with `***`).
- The dashboard never displays raw keys.

### 6.5 Profiles (optional convenience)
Two suggested presets, selectable via `--profile`:
- **`cheap`** — Haiku-class generator, `JUDGE_MODE=deterministic` (zero judge tokens), `IMAGE_PROVIDER=none`, fewer sources, `MAX_REVISIONS=1` (for testing/iteration). Only Agent 2 spends tokens.
- **`quality`** — Sonnet/Opus-class generator, `JUDGE_MODE=hybrid`, image generation on, all sources, `MAX_REVISIONS=3` (for publishing).

### 6.6 Centralized credentials (single source of truth)
- **One file, every secret:** `.env` holds all keys/tokens/passwords (LLM, Adzuna, NewsAPI, ElevenLabs, Pexels, Stability, Google OAuth, Telegram). Grouped by `# ----------` sections for readability.
- **Optional split:** set `ENV_FILE=secrets/credentials.env` to keep secrets outside the repo root; `Settings` reads whichever path `ENV_FILE` points to (defaults to `.env`).
- **`content-foundry config check`** ([Ch. 17](17-cli-interface.md#17-cli-interface)) loads `Settings`, runs all validators, and prints a **redacted** table (each secret shown as `set ✓` / `missing ✗`, never the value) so you can confirm everything is wired without leaking anything.
- **Never committed:** `.env`, `secrets/`, and `*.env` are gitignored; only `.env.example` is tracked.

---

---
[← Index](README.md) · [← Prev](05-database-schema.md) · [Next →](07-agent-1-data-fetcher.md)
