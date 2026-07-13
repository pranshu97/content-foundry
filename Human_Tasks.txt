------------------------------------------------------------------------
0. PREREQUISITES
------------------------------------------------------------------------
[ ] Windows 10/11 (these notes use PowerShell).
[ ] Python 3.11  (3.11.x — NOT 3.12/3.13; some deps target 3.11).
        Easiest via Miniconda: https://docs.conda.io/en/latest/miniconda.html
[ ] Git: https://git-scm.com/download/win
[ ] (opt) A GPU (NVIDIA/Intel/AMD) for fast video encoding — see step 1.

------------------------------------------------------------------------
1. INSTALL FFMPEG  (required — all rendering runs through it)
------------------------------------------------------------------------
[ ] Install the Gyan.dev build (it has WORKING GPU/NVENC support):
        winget install Gyan.FFmpeg.Essentials
    NOTE: the plain "ffmpeg" winget package does NOT link CUDA, so NVIDIA
    NVENC silently fails and you fall back to CPU (slower). Use the Gyan build.
[ ] Open a NEW terminal and verify:  ffmpeg -version
    (If "not found": restart the terminal so PATH refreshes, or set
     FFMPEG_PATH in .env to the full path of ffmpeg.exe.)
[ ] GPU encoding is automatic (VIDEO_ENCODER=auto): it probes and picks
    h264_nvenc / h264_qsv / h264_amf, else CPU libx264. Nothing to configure.

------------------------------------------------------------------------
2. GET THE CODE + ENVIRONMENT + INSTALL
------------------------------------------------------------------------
[ ] From the repo root, create + activate the env and install:
        conda create -n content_foundry python=3.11 -y
        conda activate content_foundry
        pip install -r requirements.txt
        pip install -e .            # exposes the `content-foundry` CLI
[ ] Verify the CLI:  content-foundry --help
    (If it's not on PATH, call the env's script directly:
     & "$env:USERPROFILE\.conda\envs\content_foundry\Scripts\content-foundry.exe" --help)

------------------------------------------------------------------------
3. INITIALIZE THE DATABASE
------------------------------------------------------------------------
[ ] python scripts/init_db.py
    (creates data/content_foundry.db — tracks runs, freshness, budget)

------------------------------------------------------------------------
4. CREATE YOUR .env
------------------------------------------------------------------------
[ ] Copy the template:  Copy-Item .env.example .env
[ ] Fill in the keys from steps 5-11 below.

------------------------------------------------------------------------
5. LLM — SCRIPT WRITING  (required; this setup uses Google Gemini, free)
------------------------------------------------------------------------
[ ] Get a FREE Gemini key at Google AI Studio:
        https://aistudio.google.com/apikey
[ ] In .env:
        PRIMARY_PROVIDER=google
        GOOGLE_API_KEY=<your key>
        GOOGLE_MODELS=gemini-2.5-flash,gemini-2.5-flash-lite
    (List several best-first; each free model has its own quota, so more =
     more runway before exhausted quota. Anthropic/OpenAI/local Ollama also work —
     set PRIMARY_PROVIDER + the matching key instead.)

------------------------------------------------------------------------
6. VISUALS — IMAGES + B-ROLL  (free)
------------------------------------------------------------------------
[ ] Thumbnail/scene images, free & no key:  IMAGE_PROVIDER=pollinations
[ ] B-roll stock video (free keys — a big quality win; get BOTH):
        Pexels  -> https://www.pexels.com/api/       -> PEXELS_API_KEY=
        Pixabay -> https://pixabay.com/api/docs/      -> PIXABAY_API_KEY=
    (Two sources = a bigger, more varied clip pool. With no B-roll key the
     video still renders using generated title cards.)

------------------------------------------------------------------------
7. VOICEOVER (TTS)
------------------------------------------------------------------------
[ ] FREE, no key (recommended to start): Microsoft Edge neural voices
        TTS_PROVIDER=edge
        TTS_VOICE_MALE=en-US-GuyNeural
        TTS_VOICE_FEMALE=en-US-AriaNeural
    (Narrator alternates male/female by run number.)
[ ] (opt) Higher quality, paid: ElevenLabs — https://elevenlabs.io
        TTS_PROVIDER=elevenlabs ; ELEVENLABS_API_KEY=... ; TTS_VOICE_ID=Rachel

------------------------------------------------------------------------
8. YOUTUBE PUBLISHING  (only when you're ready to upload)
------------------------------------------------------------------------
[ ] Google Cloud Console: https://console.cloud.google.com/
        a) Create a project.
        b) APIs & Services -> Library -> enable "YouTube Data API v3".
        c) OAuth consent screen: type External; add YOURSELF as a Test user.
        d) Credentials -> Create credentials -> OAuth client ID ->
           Application type "Desktop app".
        e) Download the JSON -> save it as  secrets/client_secrets.json
[ ] The first publish opens a browser to grant access; the token is cached
    to secrets/youtube_token.json. THE UPLOAD CHANNEL = whichever account /
    brand you pick on that consent screen. For multiple channels, create one
    token per channel and point YOUTUBE_TOKEN_FILE at the right one per run:
        YOUTUBE_TOKEN_FILE=secrets/token_<channel>.json
[ ] Keep the SAFE defaults: PUBLISH_MODE=draft, YOUTUBE_PRIVACY_STATUS=private
    -> every video uploads Private for you to review before going public.

------------------------------------------------------------------------
9. NOTIFICATIONS — TELEGRAM  (opt, free)
------------------------------------------------------------------------
[ ] Create a bot: message @BotFather -> /newbot -> copy the token.
[ ] Get your chat id: message @userinfobot -> copy the numeric id.
[ ] In .env:
        NOTIFY_ENABLED=true ; NOTIFIER=telegram
        TELEGRAM_BOT_TOKEN=<token> ; TELEGRAM_CHAT_ID=<id>
    (Or set NOTIFY_ENABLED=false to skip notifications entirely.)

------------------------------------------------------------------------
10. SOUND EFFECTS  (opt)
------------------------------------------------------------------------
[ ] A local SFX library ships in data/sounds. To mix effects in:
        SFX_ENABLED=true
[ ] (opt) Auto-download missing effects — Freesound key:
        https://freesound.org/apiv2/apply/   -> FREESOUND_API_KEY=

------------------------------------------------------------------------
11. BUDGET GUARD  (opt but recommended)
------------------------------------------------------------------------
[ ] MONTHLY_BUDGET_USD=20 ; ENFORCE_BUDGET_CAP=true
    (hard-stops a run once estimated month-to-date spend hits the cap; on
     the free Gemini + edge + Pexels stack real spend is ~$0.)

------------------------------------------------------------------------
12. FIRST RUN  (verify the whole pipeline)
------------------------------------------------------------------------
[ ] Smoke test (no media, stops at the Judge):
        content-foundry run --niche "tech careers" --to-stage judge
[ ] Full local render (no upload) — then watch output/runs/<id>/video.mp4:
        content-foundry run --niche "your niche" --idea "your idea" --to-stage render
[ ] Publish the reviewed draft when happy:
        content-foundry run --run-id <id> --from-stage publish
[ ] (opt) Review dashboard:  streamlit run dashboard/app.py
