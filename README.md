# Content Foundry

An autonomous, fully-resumable multi-agent pipeline that turns real labor-market data into a
published (Private/Unlisted draft) YouTube video — grounded in data, gated by a
strict quality rubric, and compliant with synthetic-content disclosure by default.

**Pipeline:** Data Fetcher → Script Generator → Judge → Voiceover → Visuals → Render → Publish

> The complete engineering specification (single source of truth) lives in [`spec/`](spec/README.md).
> A high-level architecture summary is in [`TECH_REPORT.md`](TECH_REPORT.md), and the operator guide
> in [`Tutorial.md`](Tutorial.md). Build learnings & conventions are tracked in [`Reference.md`](Reference.md).

## Live channel

Watch the output live: **[youtube.com/@TheCrackedEng](https://www.youtube.com/@TheCrackedEng)**

> **Disclaimer:** This channel is 100% generated, voiced, and published autonomously by this repository.

## Quickstart

```bash
# 1. Create & activate a Python 3.11+ environment, then install
pip install -r requirements.txt
pip install -e .            # exposes the `content-foundry` CLI

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
scripts/              # init_db, seed_demo
tests/                # unit / agent / integration / e2e (dry-run)
spec/                 # the authoritative specification (25 chapters)
output/runs/<run_id>/ # per-run artifacts + media + package.md
```

## Cost discipline

Only **Agent 2 (Script Generator)** always calls an LLM. The Data Fetcher, most of the Judge, and
the Visuals prompt-builder are deterministic Python — free, fast, and hallucination-proof.

Cost levers (cheapest first):
- **Run the LLM locally** — `PRIMARY_PROVIDER=local` (Ollama / LM Studio / vLLM) makes generation free.
- **Free voice** — `TTS_PROVIDER=edge` (Microsoft neural, free, no key) or `piper` (fully offline), or `chatterbox` to **clone your own voice** free & locally (MIT-licensed, safe to monetize; GPU recommended). Paid: elevenlabs / openai. Voices auto-alternate male/female by run number.
- **Free visuals** — `IMAGE_PROVIDER=none` renders polished title cards; add free Pexels + Pixabay keys for real, moment-matched B-roll (a clip per narration beat).
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

## Testing

```bash
pytest                # unit + agent + integration + e2e dry-run, ≥85% coverage gate
```

All tests run offline — vendors are mocked behind their protocols; no real network or API calls.
