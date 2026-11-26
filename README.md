# Content Foundry

An autonomous, fully-resumable multi-agent pipeline that turns one topic into a published
(Private/Unlisted draft) YouTube video — researched from live sources, gated by a strict quality
rubric, and compliant with synthetic-content disclosure by default. It is niche-agnostic: free web
research is the default source, so it works on any subject out of the box.

**Core stages:** Data Fetcher → Script Generator → Judge → Voiceover → Visuals → Render → Publish

Around that spine sit smaller specialists that only run when they are needed — an instruction
planner, a researcher, b-roll / dual-channel scene-image / motion-graphics / thumbnail directors, a
pre-render storyboard critic, and a pronunciation director.

> The complete engineering specification (single source of truth) lives in [`spec/`](spec/README.md).
> A high-level architecture summary is in [`TECH_REPORT.md`](TECH_REPORT.md), and the operator guide
> in [`Tutorial.md`](Tutorial.md).

## Quickstart

```bash
# 1. Install (Python 3.11+). From PyPI:
pip install content-foundry            # the `content-foundry` CLI + the default pipeline
# …or from source for local development (also installs the pinned local-ML stack):
#   pip install -r requirements.txt && pip install -e .
# Install headless Chromium once for the animated 1080p motion-graphics engine:
playwright install chromium

# 2. Configure
cp .env.example .env        # fill in your keys (see Human_Tasks.md)

# 3. Initialise the database
python scripts/init_db.py

# 4. Smoke test (no upload, stops at the Judge)
content-foundry run --niche "tech careers" --to-stage judge

# Or produce a vertical YouTube Short instead of a long video (one switch):
content-foundry run --niche "tech careers" --idea "your topic" --format short
```

See [`spec/23-deployment-instructions.md`](spec/23-deployment-instructions.md) for full deployment,
[`spec/17-cli-interface.md`](spec/17-cli-interface.md) for every command, and
[`Human_Tasks.md`](Human_Tasks.md) for the manual setup checklist (API keys, OAuth, Telegram bot).

## Project layout

```
src/content_foundry/    # the engine (models, agents, providers, pipeline, production, ...)
dashboard/            # Streamlit review dashboard
scripts/              # init_db, seed_demo, backup_secrets, recategorize, check_no_secrets, verify_templates
tests/                # unit / agent / integration / e2e (dry-run)
spec/                 # the authoritative specification (26 chapters)
output/runs/<run_id>/ # per-run artifacts + media + package.md
```

Everything that makes a checkout *yours* - keys, OAuth token, voice sample, avatar - is gitignored.
`python scripts/backup_secrets.py` bundles them into one restorable file to keep off-machine; see
[step 14 of `Human_Tasks.md`](Human_Tasks.md).

## Cost discipline

Several agents call an LLM, but only the **Script Generator** and the **Judge** do so on every run;
the directors (b-roll, scene image, motion graphics, thumbnail, pronunciation) and the pre-render
storyboard critic fire at a cheap `LIGHT` tier, and the planner and researcher run when you pass
`--instructions` or enable research. The Data Fetcher, 3-tier visual router, chapters, SEO metadata,
verified source provenance, and every hard gate are deterministic Python — free, fast, and
hallucination-proof.

Cost levers (cheapest first):
- **Free Gemini text chain or local LLM** — `PRIMARY_PROVIDER=google` (free-tier AI Studio model fallback chain) or `PRIMARY_PROVIDER=local` (Ollama / LM Studio / vLLM) makes text generation free, while `GOOGLE_IMAGE_API_KEY` isolates any paid image billing strictly to images.
- **Free voice** — `TTS_PROVIDER=edge` (Microsoft neural, free, no key) or `piper` (fully offline), or **clone your own voice** free & locally with `chatterbox` (MIT) or `indextts` (IndexTTS-2, Apache-2.0 — emotion separated from timbre, tone-matched reference window, sentence/year/compound-safe chunking, and padded micro-fade silence trimming). Paid: elevenlabs / openai. Voices auto-alternate male/female by run number.
- **Free animated motion graphics & visuals** — `ENABLE_PROGRAMMATIC_GRAPHICS=true` renders deterministic 1080p30 dark-mode animated HTML/CSS/SVG templates (`big_stat_card`, `evidence_card`, `split_comparison`, `rubric_checklist`, `animated_chart`, `architecture_flow`, `code_ide_mockup`) locally via headless Chromium → FFmpeg for $0; add free Pexels + Pixabay keys for strict on-topic B-roll (`BROLL_RELEVANCE_THRESHOLD` + cliché/style blacklist). Any gap beat with no paid image provider cascades cleanly to real stock B-roll or animated motion graphics — never ugly fallback slides.
- **Pre-render Storyboard Critic** — `ENABLE_STORYBOARD_CRITIC=true` deterministically + LLM-audits the entire visual storyboard *before* spending a single paid image call, enforcing video-wide setting diversity (`MAX_SETTING_ARCHETYPE_REPEATS`), human presence (`MIN_HUMAN_PRESENCE_RATIO`), zero physical/floating text, and verified `SOURCE //` badges.
- **Your data & brief are enforced, not just read** — `--data` injects your own facts above web research; `--instructions` promotes any concrete figures you specify into citable facts (so grounding never strips them), decomposes paragraphs into atomic asks with coverage repair, and fails the Judge if a specified number is altered.
- **Free research (default)** — `ENABLED_SOURCES=search` runs free DuckDuckGo web research on your run's topic (with automatic Tavily/Brave fallback when configured and `LOW_TIER_SOURCE_DOMAINS` filtering), so it works on **any** niche out of the box; labor-market feeds (adzuna/layoffs/bls) are opt-in add-ons.
- **Free idea discovery** — `IDEA_MINING_ENABLED=true` + a free `YOUTUBE_API_KEY` mines *proven* outlier videos in your niche (views far above the channel's median) so each run builds a topic with demonstrated demand instead of a guess; best-effort, so it never blocks a run.
- **Free studio polish** — scene sound effects (`SFX_ENABLED`), procedural UI transition ticks/whooshes (`PROGRAMMATIC_SFX_ENABLED`), a vocal-carved `-30 dB` dark ambient studio bed (`ENABLE_AMBIENT_MUSIC`), scene crossfades, warm grade, and Like / Subscribe nudges are all local.
- **`--profile cheap`** — deterministic judge + no paid image API + a single revision.
- **Hard budget cap** — `ENFORCE_BUDGET_CAP=true` aborts a run once estimated month-to-date spend
  reaches `MONTHLY_BUDGET_USD` (on by default; cost safety, not just an alert).
- **Selective reuse** — re-running a stage reuses existing artifacts; `voiceover --scene N` re-voices only edited scenes, `visuals` reuses existing AI images unless `--force-images` is passed, and `shots` regenerates only AI stills.
- **`FAIL_FAST_SCORE`** (opt-in) — stop paying for revisions a hopeless script can't recover from.

Use `--profile quality` for publishing.

## Beyond the core loop

These ship on by default (or behind one flag) and are what turn a rendered file into a channel:

- **3-tier visual routing & 7 animated dark-mode templates** — `0:00–0:08` opens on a high-contrast muted-feed hook graphic, `0:00–0:30` maintains brisk narrative-aligned cuts (`MAX_TEMPLATE_DURATION_SEC`), periodic anchors fire every `45–60s` so craft never drops off after `0:30`, and a ~60% Comment Friction `rubric_checklist` card binds directly to the spoken audience question. Templates use domain-decoupled semantic badges + explicit `positive | negative | neutral` polarity.
- **Dual-channel anti-collapse AI photography** — `SceneImageDirector` pairs a bidirectional `<prev2..post2>` script window with rolling visual exclusion history, camera shot-type rotation (`wide_environment` → `medium_human_action` → `tight_physical_detail`), a video-wide setting-archetype cap, human-in-frame floors, and a total ban on floating UI or physical printed/engraved text.
- **Spoken-acronym & number prosody** — numbers, calendar years (`2026` → `"twenty twenty six"`), hyphenated compounds (`Rule-of-70` → `"Rule of seventy"`), and designations (`L5` → `"L five"`) expand cleanly before synthesis; acronyms voice as spaced capitals (`SDK` → `S D K`) with curated word exceptions (`FAANG`) plus one batched classification call per run (`PRONUNCIATION_LLM_ENABLED`) version-stamped by `RULES_VERSION`.
- **Retention shaping** — the hook is spoken verbatim at second zero; an optional open loop (`RETENTION_OPEN_LOOP_ENABLED`) is *deterministically checked to pay off* before the video ends; credential-recital guards prevent verbatim bio reading; the Judge floors engagement, insight, wit, grounding, and the ending.
- **Studio audio & post-production** — lossless PCM WAV mixing (`mix_studio_audio`) into single-pass `48 kHz` `192k` AAC with EBU R128 loudness mastering (`AUDIO_LOUDNESS_LUFS=-14`, `LRA=14`), composition-matched camera motion on stills (`IMAGE_MOTION`), playback pacing (`VIDEO_SPEED`), and Like / Subscribe nudges.
- **Thumbnails** — a director writes the image prompt from the video's description, spoken hook claim, and Double-Barrel headline, scrubs career-rank age triggers (`strip_career_rank`), and runs surgical find/replace touch-up passes (`THUMBNAIL_REFINE_TURNS`) that can never gut the draft.
- **Distribution & verified citations** — auto chapters (scaled to `VIDEO_SPEED`), YouTube-safe angle-bracket sanitization, end-screen picks, a post-privacy "watch next" top comment (`END_SCREEN_ENABLED`, `RECOMMEND_COMMENT_ENABLED`), per-video `suppress_creator_credential` / `disclaimer` fields, and an automatic `Sources:` section at the very bottom of every description listing deduplicated URLs for every source shown on screen.
- **Monetization** — optional affiliate resources (`AFFILIATE_ENABLED`) resolved *before* the script is written (curated niche catalog first, live Amazon/Educative/DesignGurus/Exponent/Fenzo resolution second), woven naturally into the middle `25–75%` of the video and placed above chapters with Amazon pinned last.
- **Idea sourcing** — `BRAINSTORM_ENABLED` proposes angles, `IDEA_MINING_ENABLED` grounds them in
  proven outlier videos from your niche.
- **Operations** — a Streamlit review dashboard, a scheduler, Telegram notifications, per-run logs,
  and full resume-from-any-stage.

## Testing

```bash
pytest                # unit + agent + integration + e2e dry-run, ≥85% coverage gate
```

All tests run offline — vendors are mocked behind their protocols; no real network or API calls.

## Live channel

Watch the output live: **[youtube.com/@TheCrackedEng](https://www.youtube.com/@TheCrackedEng)**

> **Disclaimer:** This channel is 100% generated, voiced, and published autonomously by this repository.