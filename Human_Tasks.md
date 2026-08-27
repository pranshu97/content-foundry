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
  _(Just the CLI, without the full local-ML stack? `pip install content-foundry` from PyPI. The
  from-source install above is recommended here because it also pins the GPU voice-clone stack.)_
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
- [ ] _(opt)_ **Your own voice, free, best quality — IndexTTS-2.** Emotion is disentangled from timbre, so the clone stays *you* while the delivery changes. It needs a **second conda env**, and that is not optional: IndexTTS-2 requires `numpy>=2`, while Chatterbox's pinned `diffusers==0.29.0` requires `numpy<2`. The two can never share an interpreter, so the pipeline drives IndexTTS-2 out-of-process over a line protocol.

  ```powershell
  # 1. a SEPARATE env - do not install this into content_foundry
  conda create -n content_foundry2 python=3.11 -y
  conda activate content_foundry2

  # 2. the upstream checkout (anywhere; D:\index-tts here)
  git clone https://github.com/index-tts/index-tts D:\index-tts
  cd D:\index-tts
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
  pip install -e .

  # 3. the weights (~8 GB)
  hf download IndexTeam/IndexTTS-2 --local-dir=checkpoints
  ```

  Then point the pipeline at that env — `INDEXTTS_PYTHON` is the **second env's** interpreter, not this repo's:
  ```ini
  TTS_PROVIDER=indextts
  INDEXTTS_PYTHON=C:\Users\<you>\.conda\envs\content_foundry2\python.exe
  INDEXTTS_MODEL_DIR=D:\index-tts\checkpoints
  INDEXTTS_FP16=true          # half precision; materially lower VRAM on a 6 GB card
  TTS_REFERENCE_CLIP=assets/voice_reference.wav
  ```
  Verify: `& C:\Users\<you>\.conda\envs\content_foundry2\python.exe -c "import indextts; print('ok')"`.

### Verified versions

Neither env is fully reproducible from `requirements.txt` alone — the CUDA wheels come from a
different index, and the second env is a different stack entirely. This is the combination this
project is known-good on (Windows 11, RTX 3060 Laptop, 6 GB VRAM):

| | `content_foundry` (pipeline) | `content_foundry2` (IndexTTS-2 only) |
|---|---|---|
| Python | 3.11.15 | 3.11.15 |
| torch | 2.6.0+cu124 | 2.8.0+cu128 |
| numpy | <2.0 (Chatterbox/diffusers) | 2.2.6 |
| transformers | 4.44.2 (pinned) | 4.52.1 |
| installed from | `requirements.txt` + cu124 index | `pip install -e D:\index-tts` + cu128 index |

Also external to pip, so back them up or be ready to re-fetch:
- **ffmpeg** 8.1.1 (Gyan essentials build) — on PATH, or `FFMPEG_PATH` in `.env`
- **IndexTTS-2 checkpoints** ~8 GB in `D:\index-tts\checkpoints` (re-downloadable with the `hf download` above)
- **Piper voice** `.onnx` + `.onnx.json`, only if `TTS_PROVIDER=piper`
- **`assets/voice_reference.wav`** and **`assets/avatar.png`** — yours, irreplaceable; see step 14

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
- [ ] Keep the **safe defaults** — `PUBLISH_MODE=draft`, and `YOUTUBE_PRIVACY_STATUS=unlisted` (or `private`) — so every video uploads as unlisted (link-only, not surfaced publicly) for you to review before going public. The disclosure gate still hard-blocks *public* until you set "Altered or synthetic content" in Studio.
- [ ] _(opt)_ **Pull viewers to your channel.** Set `YOUTUBE_CHANNEL_URL=https://www.youtube.com/@YourHandle` so the subscribe/explore CTA appended to every description links to your channel (`CHANNEL_CTA_ENABLED=true` by default).
- [ ] _(opt)_ **"Watch next" comment (on by default).** `RECOMMEND_COMMENT_ENABLED=true` posts a comment linking your most related PRIOR uploads (the same picks as the end-screen sidecar) right after publishing, on both long-form and Shorts — it starts working once you have prior published runs. Like the top comment it needs the `youtube.force-ssl` scope, so **delete your `YOUTUBE_TOKEN_FILE` once and re-run a publish to re-consent** (the scope is requested whenever this OR `PUBLISH_TOP_COMMENT` is on). The API can't *pin* — pin it once in Studio.
- [ ] _(opt)_ **Auto top comment.** `PUBLISH_TOP_COMMENT=true` also posts the subscribe/affiliate CTA in that comment after upload. Same `youtube.force-ssl` scope + re-consent as above. The API can't *pin* a comment — pin it once in YouTube Studio.

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

## 13. Affiliate links _(opt, monetization)_

Off by default. When on, topic-relevant resource links + a disclosure are appended to every
description (and the top comment). You paste ONLY your referral link/tag per platform — the pipeline
picks which to show per video (no per-video product curation). Blank platforms are skipped.

- [ ] **Amazon Associates** (books/gear): join at `affiliate-program.amazon.com` → copy your tracking
  tag (looks like `yourname-20`) → `AMAZON_ASSOC_TAG=yourname-20`. Note: Amazon needs **3 qualifying
  sales within 180 days** to keep the account (and to unlock the Product Advertising API later); until
  then the pipeline finds products via web search and appends your tag.
- [ ] **AlgoExpert** (`algoexpert.io`): apply to their affiliate / student-ambassador program → paste
  your referral link → `AFFILIATE_ALGOEXPERT_URL=`.
- [ ] **Exponent** (`tryexponent.com`): join their affiliate/partner program (their Partners page, often
  via Impact) → `AFFILIATE_EXPONENT_URL=`.
- [ ] **LeetCode**: no public affiliate program at time of writing — if you have a referral/creator
  link set `AFFILIATE_LEETCODE_URL=`, otherwise leave blank (it's skipped).
- [ ] **Coursera** (`coursera.org`): join via the **Impact** network → generate your tracking/deep link
  → `AFFILIATE_COURSERA_URL=`.
- [ ] **Udemy**: join Udemy Affiliate (via Impact / other networks) → `AFFILIATE_UDEMY_URL=`.
- [ ] **Educative** (`educative.io`): join their affiliate program → `AFFILIATE_EDUCATIVE_URL=`.
- [ ] **DesignGurus** (`designgurus.io`): join their affiliate program → set `AFFILIATE_DESIGNGURUS_URL=`
  (a full referral URL) or `AFFILIATE_DESIGNGURUS_ID=` (just your affiliate id; a real Grokking course
  link is resolved per video when the topic fits).
- [ ] Turn it on: `AFFILIATE_ENABLED=true` (tune `AFFILIATE_MAX_LINKS`, `AFFILIATE_IN_COMMENT`).
- [ ] **Comment + pin:** set `PUBLISH_TOP_COMMENT=true` to post the resources as a comment (needs the
  `youtube.force-ssl` scope — delete your `YOUTUBE_TOKEN_FILE` once to re-consent). The Data API
  **cannot pin** a comment; pin it with one click in Studio (Comments → ⋮ → Pin).
- [ ] **Disclosure:** the description/comment already include an affiliate-disclosure line (kept for
  FTC + Amazon's terms). For any *paid sponsorship* (not plain affiliate links) also tick **"Includes
  paid promotion"** in Studio.

_The script may also SAY "link in the description" for a resource that fits the video (higher CTR), but
it will never claim to have personally used a product._

## 14. Back up what git does **not** have

Everything that makes this checkout *yours* is deliberately gitignored — the keys, the OAuth token,
your voice sample and face, your curated catalog, your private notes. A fresh clone plus a dead
laptop leaves you unable to publish as your own channel again. Bundle them into one restorable file:

```powershell
python scripts/backup_secrets.py            # -> secrets_backup.txt (~15 MB, binaries base64'd)
python scripts/backup_secrets.py --keys-only   # small text-only dump, credentials only
```

- [ ] Copy `secrets_backup.txt` somewhere off this machine (Drive, password manager vault, etc.).
- [ ] Re-run it whenever you rotate a key or re-record `voice_reference.wav`.

On a new machine, after cloning and building the env:

```powershell
python scripts/backup_secrets.py --verify secrets_backup.txt   # checksums still good?
python scripts/backup_secrets.py --restore secrets_backup.txt  # writes the files back
```

Restore refuses to overwrite files that already exist (pass `--force` to insist), and refuses to
restore at all if any checksum fails — so a copy that got mangled in transit is caught *before* it
overwrites a good one.

> **That file is a live credential.** It holds every key in plaintext. It is gitignored *and* blocked
> by the pre-commit secrets hook, so it cannot be committed by accident — but treat it like the keys
> themselves: never paste it into a chat, an issue, or a shared drive folder.
>
> Not included, by design: `data/content_foundry.db` (rebuild with `scripts/init_db.py`),
> `assets/avatar.cutout.png` (regenerated), and `output/runs/**` (finished videos — back those up
> separately if you want to keep them).
