# Content Foundry

An autonomous, fully-resumable multi-agent pipeline that turns one topic into a published
(Private/Unlisted draft) YouTube video — researched from live sources, gated by a strict quality
rubric, and compliant with synthetic-content disclosure by default. It is niche-agnostic: free web
research is the default source, so it works on any subject out of the box.

**Core stages:** Data Fetcher → Script Generator → Judge → Voiceover → Visuals → Render → Publish

Around that spine sit smaller specialists that only run when they are needed — an instruction
planner, a researcher, b-roll / scene-image / thumbnail directors, and a pronunciation director.

> The complete engineering specification (single source of truth) lives in [`spec/`](spec/README.md).
> A high-level architecture summary is in [`TECH_REPORT.md`](TECH_REPORT.md), and the operator guide
> in [`Tutorial.md`](Tutorial.md).

## Quickstart

```bash
# 1. Install (Python 3.11+). From PyPI:
pip install content-foundry            # the `content-foundry` CLI + the default pipeline
# …or from source for local development (also installs the pinned local-ML stack):
#   pip install -r requirements.txt && pip install -e .

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
src/content_foundry/    # the engine (models, agents, providers, pipeline, ...)
dashboard/            # Streamlit review dashboard
scripts/              # init_db, seed_demo, backup_secrets, recategorize, check_no_secrets
tests/                # unit / agent / integration / e2e (dry-run)
spec/                 # the authoritative specification (26 chapters)
output/runs/<run_id>/ # per-run artifacts + media + package.md
```

Everything that makes a checkout *yours* - keys, OAuth token, voice sample, avatar - is gitignored.
`python scripts/backup_secrets.py` bundles them into one restorable file to keep off-machine; see
[step 14 of `Human_Tasks.md`](Human_Tasks.md).

## Cost discipline

Several agents call an LLM, but only the **Script Generator** and the **Judge** do so on every run;
the directors (b-roll, scene image, thumbnail, pronunciation) fire once per run at a cheap tier, and
the planner and researcher only when you pass `--instructions` or enable research. The Data Fetcher,
chapters, SEO metadata, chart rendering and every hard gate are deterministic Python — free, fast,
and hallucination-proof.

Cost levers (cheapest first):
- **Run the LLM locally** — `PRIMARY_PROVIDER=local` (Ollama / LM Studio / vLLM) makes generation free.
- **Free voice** — `TTS_PROVIDER=edge` (Microsoft neural, free, no key) or `piper` (fully offline), or **clone your own voice** free & locally with `chatterbox` (MIT) or `indextts` (IndexTTS-2, Apache-2.0 — emotion separated from timbre, and it picks the reference window whose delivery matches the script's tone). Paid: elevenlabs / openai. Voices auto-alternate male/female by run number.
- **Free visuals** — `IMAGE_PROVIDER=none` renders polished title cards; add free Pexels + Pixabay keys for real, moment-matched B-roll (a clip per narration beat), now held to a STRICT on-topic bar — a beat with no confidently-relevant clip falls back to a bespoke generated image (or a clean card when no image provider is set), never an off-topic clip.
- **Free charts** — a levelling matrix, a comparison of magnitudes, a tier ladder or a short pipeline is **drawn with matplotlib** rather than sent to an image model (`DIAGRAMS_ENABLED`): no API call, and the labels come out exact instead of as a model's guess at lettering. The spec that produced each chart is persisted, so it can be corrected and redrawn for free without going back to the model.
- **Your brief is enforced, not just read** — `--instructions` is decomposed into *atomic asks* (one sentence usually carries five or six), routed to research and to the writer separately, and any ask the plan missed is re-planned. If the script then changes a figure you specified — a brief saying "ten percent" coming back as "five percent" — the Judge rejects it outright.
- **Free research (default)** — `ENABLED_SOURCES=search` runs free DuckDuckGo web research on your run's topic (no key), so it works on **any** niche out of the box; the labor-market feeds (adzuna/layoffs/bls) are opt-in add-ons.
- **Free idea discovery** — `IDEA_MINING_ENABLED=true` + a free `YOUTUBE_API_KEY` mines *proven* outlier videos in your niche (views far above the channel's median) so each run builds a topic with demonstrated demand instead of a guess; best-effort, so it never blocks a run.
- **Free polish** — bundled sound effects (`SFX_ENABLED`), scene crossfades, a warm grade, and a Subscribe nudge are all local/ffmpeg (no paid services).
- **`--profile cheap`** — deterministic judge + Pillow cards (no image API) + a single revision.
- **Hard budget cap** — `ENFORCE_BUDGET_CAP=true` aborts a run once estimated month-to-date spend
  reaches `MONTHLY_BUDGET_USD` (on by default; cost safety, not just an alert).
- **Resume reuses paid artifacts** — re-running a stage reuses existing voiceover/visuals instead of
  paying again (use `--force` to regenerate).
- **`FAIL_FAST_SCORE`** (opt-in) — stop paying for revisions a hopeless script can't recover from.

Use `--profile quality` for publishing.

## Beyond the core loop

These ship on by default (or behind one flag) and are what turn a rendered file into a channel:

- **Spoken-acronym control** — acronyms are voiced as spaced capitals (`SDK` → `S D K`), with a
  curated table for the ones that are really words (`FAANG` → "fang"). Anything uncurated gets one
  cheap classification call per run (`PRONUNCIATION_LLM_ENABLED`), cached per run and invalidated
  automatically when the spelling rules change.
- **Retention shaping** — the hook is spoken, not just written; an optional open loop
  (`RETENTION_OPEN_LOOP_ENABLED`) is *deterministically checked to pay off* before the video ends;
  the Judge floors engagement, insight, wit and the ending.
- **Post-production** — composition-matched camera motion on stills (`IMAGE_MOTION`), EBU R128
  loudness mastering (`AUDIO_LOUDNESS_LUFS`), playback pacing (`VIDEO_SPEED`), sound-effect mixing,
  and like / subscribe badges.
- **Thumbnails** — a director writes the image prompt from the video's own description, then a
  low-temperature critique pass returns *edits*, never a rewrite (`THUMBNAIL_REFINE_TURNS`), so a
  refinement can only improve or no-op.
- **Distribution** — auto chapters, SEO title/description/tags, end-screen picks and a "watch next"
  top comment built from your own prior uploads (`END_SCREEN_ENABLED`, `RECOMMEND_COMMENT_ENABLED`).
- **Monetization** — optional affiliate resources (`AFFILIATE_ENABLED`) resolved *before* the script
  is written, so the narration never promises a link the description cannot deliver. You supply only
  a referral tag per platform; a local catalog is matched to the topic, and nothing attaches when
  nothing genuinely fits.
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