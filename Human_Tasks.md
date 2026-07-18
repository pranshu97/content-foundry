# Content Foundry — Setup Checklist

Things a **human** must do to get this running from scratch. The agent can write code and config, but it can't create accounts, click through OAuth screens, install system software, or read your secrets.

> **Legend:** `[ ]` = to do · `[x]` = done · _(opt)_ = optional
>
> **Tip:** in `.env`, put comments on their **own line** for blank values — an inline `# ...` after a blank key gets captured *as* the value (a dotenv gotcha).

## 0. Prerequisites

- [ ] Windows 10/11 (these notes use PowerShell).
- [ ] **Python 3.11** (3.11.x — *not* 3.12/3.13; some deps target 3.11). Easiest via [Miniconda](https://docs.conda.io/en/latest/miniconda.html).
- [ ] [Git for Windows](https://git-scm.com/download/win)
- [ ] _(opt)_ A GPU — **NVIDIA** (recommended) for fast video encoding **and** free local voice cloning (Chatterbox); Intel/AMD accelerate encoding only. See step 1 (encoding) and step 7 (voice cloning).

## 1. Install ffmpeg — required (all rendering runs through it)

- [ ] Install the Gyan.dev build (it has **working GPU/NVENC** support):
  ```powershell
  winget install Gyan.FFmpeg.Essentials
  ```
  > The plain `ffmpeg` winget package does **not** link CUDA, so NVIDIA NVENC silently fails and you fall back to CPU (slower). Use the Gyan build.
- [ ] Open a **new** terminal and verify: `ffmpeg -version`. If "not found", restart the terminal so PATH refreshes, or set `FFMPEG_PATH` in `.env` to the full path of `ffmpeg.exe`.
- [ ] GPU encoding is automatic (`VIDEO_ENCODER=auto`): it probes and picks `h264_nvenc` / `h264_qsv` / `h264_amf`, else CPU `libx264`. Nothing to configure.

## 2. Get the code + environment + install

- [ ] From the repo root, create + activate the env and install:
  ```powershell
  conda create -n content_foundry python=3.11 -y
  conda activate content_foundry
  pip install -r requirements.txt
  pip install -e .            # exposes the `content-foundry` CLI
  ```
- [ ] Verify the CLI: `content-foundry --help`. If it's not on PATH, call the env's script directly:
  ```powershell
  & "$env:USERPROFILE\.conda\envs\content_foundry\Scripts\content-foundry.exe" --help
  ```

## 3. Initialize the database

- [ ] Run `python scripts/init_db.py` — creates `data/content_foundry.db` (tracks runs, freshness, budget).

## 4. Create your `.env`

- [ ] Copy the template: `Copy-Item .env.example .env`
- [ ] Fill in the keys from steps 5–11 below.

## 5. LLM — script writing (required; this setup uses Google Gemini, free)

- [ ] Get a **free** Gemini key at [Google AI Studio](https://aistudio.google.com/apikey).
- [ ] In `.env`:
  ```ini
  PRIMARY_PROVIDER=google
  GOOGLE_API_KEY=<your key>
  GOOGLE_MODELS=gemini-2.5-flash,gemini-2.5-flash-lite
  ```
  List several best-first; each free model has its own quota, so more = more runway before it's exhausted. (Anthropic / OpenAI / local Ollama also work — set `PRIMARY_PROVIDER` + the matching key instead.)

## 6. Visuals — images + B-roll (free)

- [ ] Thumbnail/scene images, free & no key: `IMAGE_PROVIDER=pollinations`
- [ ] B-roll stock video (free keys — a big quality win; get **both**):
  - Pexels — [pexels.com/api](https://www.pexels.com/api/) → `PEXELS_API_KEY=`
  - Pixabay — [pixabay.com/api/docs](https://pixabay.com/api/docs/) → `PIXABAY_API_KEY=`

  Two sources = a bigger, more varied clip pool. With no B-roll key the video still renders using generated title cards.

## 7. Voiceover (TTS)

- [ ] **Free, no key** (recommended to start) — Microsoft Edge neural voices:
  ```ini
  TTS_PROVIDER=edge
  TTS_VOICE_MALE=en-US-GuyNeural
  TTS_VOICE_FEMALE=en-US-AriaNeural
  ```
  The narrator alternates male/female by run number.
- [ ] _(opt)_ Higher quality, paid: [ElevenLabs](https://elevenlabs.io) — `TTS_PROVIDER=elevenlabs`, `ELEVENLABS_API_KEY=...`, `TTS_VOICE_ID=Rachel`
- [ ] _(opt)_ **Your own voice, free** — clone it locally with Chatterbox (MIT-licensed, safe to monetize):
  ```ini
  TTS_PROVIDER=chatterbox
  TTS_REFERENCE_CLIP=assets/voice_reference.wav   # a ~20-30s clean WAV of you speaking
  TTS_CLONE_DEVICE=cuda                            # cuda (NVIDIA GPU) | cpu
  ```
  - `pip install chatterbox-tts`
  - **GPU — strongly recommended (~5x faster).** The default torch is CPU-only; for your NVIDIA GPU install the CUDA build:
    ```powershell
    pip uninstall -y torch torchaudio
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
    ```
    Verify with `python -c "import torch; print(torch.cuda.is_available())"` → `True`. With `TTS_CLONE_DEVICE=cuda` the run hard-fails if the GPU isn't visible (so it never silently falls back to slow CPU).

## 8. YouTube publishing (only when you're ready to upload)

- [ ] In the [Google Cloud Console](https://console.cloud.google.com/):
  1. Create a project.
  2. **APIs & Services → Library** → enable **YouTube Data API v3**.
  3. **OAuth consent screen**: type *External*; add **yourself** as a Test user.
  4. **Credentials → Create credentials → OAuth client ID** → application type **Desktop app**.
  5. Download the JSON → save it as `secrets/client_secrets.json`.
- [ ] The first publish opens a browser to grant access; the token is cached to `secrets/youtube_token.json`. **The upload channel = whichever account/brand you pick on that consent screen.** For multiple channels, create one token per channel and point `YOUTUBE_TOKEN_FILE` at the right one per run:
  ```ini
  YOUTUBE_TOKEN_FILE=secrets/token_<channel>.json
  ```
- [ ] Keep the **safe defaults** — `PUBLISH_MODE=draft`, `YOUTUBE_PRIVACY_STATUS=private` — so every video uploads Private for you to review before going public.
- [ ] _(opt)_ **Pull viewers to your channel.** Set `YOUTUBE_CHANNEL_URL=https://www.youtube.com/@YourHandle` so the subscribe/explore CTA appended to every description links to your channel (`CHANNEL_CTA_ENABLED=true` by default).
- [ ] _(opt)_ **Auto top comment.** `PUBLISH_TOP_COMMENT=true` posts the CTA as a comment after upload. It needs the broader `youtube.force-ssl` scope, so **delete your `YOUTUBE_TOKEN_FILE` (e.g. `secrets/youtube_token.json`) and re-run a publish to re-consent** with the new scope. The API can't *pin* a comment — pin it once in YouTube Studio.

### _(opt)_ Vertical YouTube Shorts

Flip `CONTENT_FORMAT=short` in `.env` (or add `--format short` to a `run`) to produce a vertical 9:16
~50s Short instead of a long video — same pipeline, same commands, no extra setup.

### _(opt)_ Proven-idea mining — real outlier videos as pre-vetted ideas

Uses a **read-only** YouTube Data API v3 **key** (separate from the OAuth publish creds above) to surface videos that beat their own channel's median views, tagged as proof in the idea picker.
- [ ] Same Google Cloud project → **Credentials → Create credentials → API key**; restrict it to **YouTube Data API v3**.
- [ ] In `.env`:
  ```ini
  IDEA_MINING_ENABLED=true
  YOUTUBE_API_KEY=<your read-only Data-API key>
  IDEA_MINING_OUTLIER_MULTIPLE=3   # a video qualifies at >= N x its channel's median views
  ```
  Leave `IDEA_MINING_CHANNELS` blank to search videos by your topic (most relevant), or pin `@handles` / URLs / `UC…` ids.

## 9. Notifications — Telegram _(opt, free)_

- [ ] Create a bot: message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
- [ ] Get your chat id: message [@userinfobot](https://t.me/userinfobot) → copy the numeric id.
- [ ] In `.env`:
  ```ini
  NOTIFY_ENABLED=true
  NOTIFIER=telegram
  TELEGRAM_BOT_TOKEN=<token>
  TELEGRAM_CHAT_ID=<id>
  ```
  (Or set `NOTIFY_ENABLED=false` to skip notifications entirely.)

## 10. Sound effects _(opt)_

- [ ] A local SFX library ships in `data/sounds`. To mix effects in: `SFX_ENABLED=true`
- [ ] _(opt)_ Auto-download missing effects — get a [Freesound key](https://freesound.org/apiv2/apply/) → `FREESOUND_API_KEY=`

## 11. Budget guard _(opt but recommended)_

- [ ] Set `MONTHLY_BUDGET_USD=20` and `ENFORCE_BUDGET_CAP=true` — hard-stops a run once estimated month-to-date spend hits the cap. On the free Gemini + Edge + Pexels stack, real spend is ~$0.

## 12. First run (verify the whole pipeline)

- [ ] Smoke test (no media, stops at the Judge):
  ```powershell
  content-foundry run --niche "tech careers" --to-stage judge
  ```
- [ ] Full local render (no upload) — then watch `output/runs/<id>/video.mp4`:
  ```powershell
  content-foundry run --niche "your niche" --idea "your idea" --to-stage render
  ```
- [ ] Publish the reviewed draft when happy:
  ```powershell
  content-foundry run --run-id <id> --from-stage publish
  ```
- [ ] _(opt)_ Review dashboard:
  ```powershell
  streamlit run dashboard/app.py
  ```
