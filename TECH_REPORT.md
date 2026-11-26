# Content Foundry — Technical Report

**A resumable, grounded, quality-gated multi-agent pipeline for automated long-form video.**

| | |
|---|---|
| **Version** | 1.1.0 |
| **Language** | Python ≥ 3.11 |
| **Source** | 100 modules · ~26,600 LOC · 17 prompts · 295 typed settings |
| **Tests** | 71 files · 994 passing · `ruff` + `mypy` + `no-secrets` clean |
| **Design spec** | 26 chapters (`spec/`) |
| **License** | MIT |

---

## Abstract

Content Foundry converts **any topic** (grounded in live web research, creator-supplied `--data` /
`--instructions`, and optional labor-market feeds) into a **finished, upload-ready YouTube video or
vertical Short** through seven core stages and specialized sub-agents. The system is built around four
hard guarantees that distinguish it from a naive "prompt-to-video" script:

1. **Grounding & verified provenance** — every statistic in the final script traces back to a fetched or creator-supplied `KeyFact` (ungrounded numbers are mechanically stripped), unverified `SOURCE //` badges are rejected, and all embedded video sources are automatically deduplicated into a `Sources:` block at the very bottom of the YouTube description.
2. **Quality gating (script + storyboard)** — an automated, deterministic-first Judge scores every script against a 10-dimension rubric + hard gates, and a pre-render `StoryboardCritic` audits every visual shot *before* paid image tokens are spent.
3. **Resumability & selective editing** — each stage persists a versioned artifact (plus per-scene raw TTS clips and shot specs), so any run can be stopped, hand-edited, selectively re-voiced (`voiceover --scene N`), or resumed from an arbitrary stage without redoing prior work.
4. **Compliance by default** — synthetic-content disclosure is enforced programmatically, and nothing can auto-publish publicly without it.

A fifth cross-cutting concern, **cost control**, lets the entire pipeline run **free** (Google Gemini free-tier text chain or local Ollama + offline IndexTTS-2/Chatterbox voice cloning + local Playwright 1080p motion graphics + free stock B-roll) or pair with an isolated paid `GOOGLE_IMAGE_API_KEY` solely for bespoke photography.

Recent architecture upgrades raise both retention and perceived studio craft:
- **3-Tier Visual Router & 7 Dark-Mode Animated Templates (`motion_graphics.py`, `visual_router.py`):** deterministic routing across (1) 1080p30 HTML/CSS/SVG motion graphics rendered via headless Chromium → FFmpeg (`yuv420p` TV-range parity) for `0:00–0:08` muted feed previews, `0:00–0:30` opening beats, concrete stats/comparisons/flows/code, a beat-bound ~60% Comment Friction `rubric_checklist`, and `45–60s` periodic anchors (`MAX_TEMPLATE_DURATION_SEC=8.0s`), with domain-decoupled semantic badges + explicit polarity; (2) strict cliché-filtered stock B-roll (`BROLL_RELEVANCE_THRESHOLD` + `BROLL_STYLE_BANLIST`); and (3) **dual-channel anti-collapse AI stills** (`<prev2..post2>` window + rolling exclusion history + camera shot rotation + video-wide `MAX_SETTING_ARCHETYPE_REPEATS=2` + `MIN_HUMAN_PRESENCE_RATIO=0.35` + zero floating or physical printed/engraved text).
- **Instruction & Data Fidelity (`creator_data.py`, `instruction_planner.py`, `judge_checks.py`):** `--data` ranks creator facts first; `--instructions` enumerates atomic asks with coverage repair, promotes stated figures into `creator_supplied` `KeyFact`s, and enforces figure fidelity at the Judge.
- **Voice Realism & Prosody (`text_normalize.py`, `tts.py`, `sound_design.py`, `render_backend.py`):** natural calendar-year and hyphen-free compound expansion (`prepare_tts_text`), decimal/abbreviation-safe single-sentence TTS chunking, `55 ms` head / `85 ms` tail safety pads with `15 ms` raised-cosine micro-fades, lossless PCM WAV studio mixing (`-14 dB` UI transition cues + vocal-carved `-30 dB` dark ambient bed), and single-pass `48 kHz` `192k` AAC encoding mastered to `-14 LUFS` (`LRA=14`).
- **Distribution & Monetization (`affiliate.py`, `seo.py`, `end_screen.py`):** pre-generation affiliate resolution (curated catalog first, live Amazon/Educative/DesignGurus/Exponent + universal Fenzo AI second), post-`set_privacy` watch-next + affiliate top comments, Like/Subscribe nudges, and per-video `suppress_creator_credential` / `disclaimer` controls.

---

## Table of Contents

1. [Motivation & Goals](#1-motivation--goals)
2. [System Overview](#2-system-overview)
3. [Design Principles](#3-design-principles)
4. [Architecture](#4-architecture)
5. [The Pipeline Orchestrator](#5-the-pipeline-orchestrator)
6. [Data Model & Artifacts](#6-data-model--artifacts)
7. [The Agents](#7-the-agents)
8. [The Judge — Quality Gate](#8-the-judge--quality-gate)
9. [Provider Abstraction](#9-provider-abstraction)
10. [Safeguards & Compliance](#10-safeguards--compliance)
11. [Reliability & Error Handling](#11-reliability--error-handling)
12. [Cost Control & Local-First Operation](#12-cost-control--local-first-operation)
13. [Observability](#13-observability)
14. [Testing Strategy](#14-testing-strategy)
15. [Notable Engineering Decisions](#15-notable-engineering-decisions)
16. [Limitations & Future Work](#16-limitations--future-work)
17. [Technology Stack](#17-technology-stack)
18. [Appendix: Metrics & Glossary](#18-appendix-metrics--glossary)

---

## 1. Motivation & Goals

Automated video generators typically hallucinate facts, produce generic filler, offer no way to
intervene mid-process, and ignore platform disclosure rules. Content Foundry was designed to make
each of those failure modes structurally impossible rather than merely discouraged by a prompt.

**Functional goals**

| ID | Goal |
|----|------|
| **G1** | *Grounded content* — every script cites at least one concrete fetched data point; no hallucinated statistics. |
| **G2** | *Insight enforcement* — a quantified Insight Score gates every script; below threshold ⇒ rejected/revised. |
| **G3** | *Anti-repetition* — the Judge detects "template fatigue" across recent runs and forces a structural shift. |
| **G4** | *Full resumability* — the operator can start the pipeline at any stage from the previous stage's (optionally hand-edited) artifact. |
| **G5** | *Compliance by default* — every output carries the mandatory synthetic-content disclosure, enforced by a hard gate before anything goes public. |

**Non-functional goals:** deterministic where possible (reproducible judging, no needless token
spend), operable by a single person, and runnable at zero marginal cost.

---

## 2. System Overview

The pipeline is a linear seven-stage flow with a single feedback loop between generation and
judging:

```mermaid
flowchart LR
    A[1 · Fetch<br/><i>data_brief</i>] --> B[2 · Generate<br/><i>script</i>]
    B --> C{3 · Judge}
    C -- REVISE --> B
    C -- FAIL --> X[(stop)]
    C -- PASS --> D[4 · Voiceover<br/><i>narration.mp3</i>]
    D --> E[5 · Visuals<br/><i>thumbnail + scenes</i>]
    E --> F[6 · Render<br/><i>video.mp4</i>]
    F --> G[7 · Publish<br/><i>YouTube draft</i>]
```

| # | Stage | Responsibility | Artifact | LLM? |
|---|-------|----------------|----------|------|
| 1 | **fetch** | Pull job/salary/layoff/news signals; distill grounded facts | `data_brief.json` | No (deterministic) |
| 2 | **generate** | Write the script from the brief | `script.json` | Yes (or local) |
| 3 | **judge** | Score on 10 dimensions → PASS / REVISE / FAIL | `judge_report.json` | Optional (hybrid) |
| 4 | **voiceover** | TTS narration + word-level timings | `voiceover.json`, `narration.mp3` | No |
| 5 | **visuals** | Thumbnail, per-scene images/B-roll, captions | `visuals.json`, `assets/…` | Optional |
| 6 | **render** | Assemble audio + visuals + captions to mp4 | `video.json`, `video.mp4` | No |
| 7 | **publish** | Upload as a Private draft (or dry-run) | `publish_result.json` | No |

**Core agents (1–3)** produce a rubric-approved script; **production agents (4–7)** turn it into a
published video. Stage 3 is a hard gate: production never begins on a failing script.

---

## 3. Design Principles

**Deterministic-first.** Anything that *can* be computed with plain code *is* — data distillation
(stage 1), six of ten Judge dimensions, template-fatigue detection, SEO, and captioning all run
without tokens. LLM calls are reserved for the two genuinely subjective tasks (writing, and scoring
the four subjective rubric dimensions), which minimizes cost and maximizes reproducibility.

**Artifact-per-stage.** Every stage's output is a self-contained, schema-versioned JSON document
written to `output/runs/<run_id>/`. This single decision yields resumability, hand-editability,
auditability, and testability for free — each stage is a pure function from one artifact to the next.

**Protocol-based providers.** Every external capability (LLM, TTS, image, render, upload) sits behind
a `typing.Protocol`. Concrete providers are swapped by configuration; vendor SDKs are imported
lazily so an unused provider never needs to be installed.

**Fail closed on compliance, fail open on data.** A missing data source degrades gracefully (the run
proceeds on the sources that succeeded), but a missing disclosure hard-blocks publication.

---

## 4. Architecture

### 4.1 Package layout

```
src/content_foundry/
├── cli.py            # Typer CLI + Rich progress reporter (18 commands)
├── config.py         # pydantic-settings: 295 flat typed settings, zero-drift env sync
├── errors.py         # ContentFoundryError hierarchy
├── logging.py        # structlog configuration + per-run JSONL sink
├── scheduler.py      # APScheduler cron entry point
├── agents/           # 7 core agents + planner, researcher, directors & storyboard critic (19 files)
├── models/           # Pydantic artifact schemas (DataBrief, ResearchBrief, Script, …) (13 files)
├── providers/        # Google/Anthropic/OpenAI/Local LLM, TTS, image, B-roll, FFmpeg, YouTube (17 files)
├── datasources/      # Search (DDG/Tavily/Brave chain) + Adzuna, layoffs RSS, news, BLS (8 files)
├── pipeline/         # Orchestrator, creator_data, topic_relevance, artifacts, packaging (7 files)
├── production/       # motion_graphics (7 templates), visual_router, sound_design, seo, affiliate, … (15 files)
├── persistence/      # SQLAlchemy models + repository (4 files)
├── safeguards/       # grounding, disclosure (3 files)
├── notifications/    # Telegram / null notifier + credit monitor (5 files)
├── templates/        # the 6 rotating structural script templates (2 files)
└── prompts/          # externalized XML-structured prompt templates (17 .txt files + loader)
```

### 4.2 Layering

```mermaid
flowchart TD
    CLI[cli.py / scheduler.py] --> ORCH[pipeline.Orchestrator]
    ORCH --> AG[agents/*]
    ORCH --> REPO[persistence.Repository]
    AG --> PROV[providers/*]
    AG --> SAFE[safeguards/*]
    AG --> MODELS[models/*]
    AG --> DS[datasources/*]
    PROV --> EXT[(External APIs / local servers)]
    REPO --> DB[(SQLite)]
    ORCH --> CFG[config.Settings]
    AG --> CFG
```

The orchestrator is the only component that knows the *sequence* of stages; agents know nothing about
each other and communicate exclusively through persisted artifacts. Configuration is injected from a
single `Settings` object resolved once per process.

---

## 5. The Pipeline Orchestrator

The orchestrator (`pipeline/orchestrator.py`) is a small state machine over the run lifecycle:

```
CREATED → FETCHED → GENERATED → JUDGED → APPROVED
        → VOICED → VISUALIZED → RENDERED → PUBLISHED
  (+ REVISING during the gen⇄judge loop,  + FAILED on unrecoverable error)
```

**Responsibilities**

- **Stage sequencing** with `--from-stage` / `--to-stage` slicing.
- **Resume inference.** For a `FAILED` or partially completed run, the next stage is inferred from
  the artifacts present on disk (a dead render resumes at *render*; a failed judge retries at
  *generate*) — so a resume never needlessly re-fetches or re-pays.
- **Reuse-or-regenerate.** If a stage's artifact already exists it is reused; `--force` regenerates.
- **The generate⇄judge loop.** A `REVISE` verdict feeds the Judge's per-dimension critique back into
  a rewrite, up to `MAX_REVISIONS`. Judge attempts are persisted with a monotonic attempt number
  (offset by prior attempts on resume to avoid uniqueness collisions).
- **Budget enforcement.** Before each paid stage, estimated month-to-date spend is checked against
  `MONTHLY_BUDGET_USD`; when `ENFORCE_BUDGET_CAP` is set, the run hard-stops instead of overspending.
- **Progress reporting.** A pluggable reporter emits `start / step / done / judge / gate / end`
  events, rendered as a live Rich console view or as structured logs.

---

## 6. Data Model & Artifacts

All artifacts are Pydantic v2 models (`models/`), schema-versioned for forward compatibility. The two
central schemas:

**`DataBrief`** — the grounded, citation-ready output of stage 1.

```
DataBrief
├── key_facts: [KeyFact]          # each carries a Citation (source, url, snippet)
├── content_angles: [ContentAngle]
├── coverage: {source: bool}      # which sources succeeded
└── gaps: [str]
```

**`Script`** — the production-aware output of stage 2.

```
Script
├── hook: str
├── scenes: [SceneCue]            # narration, on_screen_text, b_roll_keywords, fact_ref
├── title_options, cta, description, tags, thumbnail_concept
├── word_count, grounded_fact_refs
└── synthetic_disclosure: bool
```

The `fact_ref` on each `SceneCue` is the linchpin of grounding: it is an index into
`DataBrief.key_facts`, and the entire grounding + source-attribution machinery is built on validating
and resolving that reference.

Every artifact carries a `Provenance` block (`produced_by`, `model`, `config_hash`) so any output can
be traced to the exact code, model, and configuration that produced it — including a
`operator_edited` marker when a human hand-tunes an artifact between stages.

---

## 7. The Agents

| Agent | Module | Summary |
|-------|--------|---------|
| **1 · Data Fetcher** | `data_fetcher.py` + `distill.py` | Pulls signals from enabled sources (the default web search **fans out into several topic + facet queries**, merged and de-duplicated for more distinct facts), ranks them, and **deterministically** distills up to `MAX_FACTS` `KeyFact`s and content angles. No LLM. Fails fast if fewer than `MIN_FACTS` grounded facts survive. |
| **2 · Script Generator** | `script_generator.py` | The single always-on LLM call. Builds a template-driven prompt, parses/repairs JSON, strips ungrounded stats, guarantees minimum length, and stamps sources. |
| **3 · Judge** | `judge.py` + `judge_checks.py` | Deterministic-first quality gate (see §8). |
| **4 · Voiceover** | `voiceover.py` | TTS synthesis with word-level timing for caption alignment. |
| **5 · Visuals** | `visuals.py` | Thumbnail + per-scene visuals (AI image, Pexels B-roll, or procedurally-drawn cards) + SRT captions. |
| **6 · Renderer** | `renderer.py` | Assembles audio + visuals + captions into an mp4 via ffmpeg. |
| **7 · Publisher** | `publisher.py` | Uploads to YouTube as a Private draft; writes the human review package. |

### 7.1 Script Generator internals

The generator's `run()` is a short, auditable pipeline of pure-ish transformations:

```
build_prompt → complete(LLM) → parse_json(+reformat retry)
   → coerce_script → repair_grounding → ensure_min_length → stamp_sources
```

- **`coerce_script`** tolerates the messy reality of small local models: an `int` field returned as a
  list (`[3, 5]`), a stringified number, a float, or `null` is normalized to a single valid value
  instead of crashing schema validation.
- **`repair_grounding`** strips any statistic whose scene lacks a valid `fact_ref`, so hallucinated
  numbers cannot survive.
- **`ensure_min_length`** detects an under-generated stub and asks once more for the full script,
  keeping whichever draft is longer.
- **`stamp_sources`** guarantees the source-attribution rule (see §10) in code.

The six rotating **structural templates** (contrarian, myth-buster, data-drop, etc.) supply distinct
beat sheets and default perspectives, and are rotated to defeat template fatigue.

---

## 8. The Judge — Quality Gate

The Judge scores ten dimensions on a 0–5 scale. **Six are computed by plain Python; at most one
cheap LLM call** scores the four subjective dimensions (Actionability, Insight, Engagement,
Wittiness), and even that is skippable via `JUDGE_MODE`.

| Dimension | Weight | Hard floor | Scored by |
|-----------|:------:|:----------:|-----------|
| Actionability | 0.14 | — | LLM (hybrid) |
| Specificity | 0.14 | — | deterministic |
| Grounding | 0.14 | **4.0** | deterministic |
| Insight | 0.14 | **3.5** | LLM (hybrid) |
| Engagement | 0.10 | **2.5** | LLM (hybrid) |
| Wittiness | 0.07 | **2.5** | LLM (hybrid) |
| Ending / Sign-off | 0.07 | **3.0** | deterministic |
| Hook & Retention | 0.10 | — | deterministic |
| Structural Freshness | 0.07 | — | deterministic |
| Compliance | 0.03 | pass/fail | deterministic |

The weights sum to 1.0, so the weighted total is a plain weighted average on 0–5:

$$\text{weighted\_total} = \frac{\sum_i w_i \, s_i}{\sum_i w_i}$$

**Grounding** is scored as the fraction of statistics that resolve to a valid fact:

$$\text{grounding} = 5 \cdot \frac{\text{grounded stats}}{\text{total stats}} \quad (=5 \text{ when there are none})$$

**Verdict logic.** A `PASS` requires the weighted total to clear `PASS_THRESHOLD` **and** every hard
gate to hold:

$$\text{PASS} \iff \text{total} \ge \tau \;\wedge\; \text{compliance} \;\wedge\; \text{grounding} \ge 4 \;\wedge\; \text{insight} \ge 3.5 \;\wedge\; \lnot\text{fatigue} \;\wedge\; \text{complete}$$

Otherwise the run `REVISE`s (feeding a per-dimension critique back to the generator) until it either
passes or exhausts `MAX_REVISIONS`, which yields `FAIL`.

**Completeness gate.** The rubric measures *quality*, not *quantity* — so a grounded but tiny stub
would otherwise score well (a short hook even scores *higher*). A dedicated deterministic gate rejects
drafts that are structurally too short:

$$\text{complete} \iff |\text{scenes}| \ge \text{MIN\_SCENES} \;\wedge\; \text{word\_count} \ge r \cdot \text{SCRIPT\_TARGET\_WORDS}$$

A single-scene draft is rejected **without** spending an LLM call, exactly like a grounding violation.

**Instruction fidelity.** A figure the creator wrote into `--instructions` must come back unchanged.
This is a *different property* from instruction coverage: a brief asking for "ten percent of the job"
was covered, discussed at length, and silently rewritten as "five percent" — while the matching
"ninety percent" survived, so the script contradicted both the instruction and itself. Coverage
checks cannot see it, because the topic *is* discussed and the words *do* overlap. The gate compares
`(value, unit)` pairs across digits and number-words, and fires **only when the script uses the same
unit with a different value** — silence is the coverage checker's problem, and extra figures the
writer researched are never penalised, so the check cannot decay into noise.

**Cost-saving short-circuit.** When a deterministic hard gate already decides the verdict (e.g. a
grounding or completeness violation), the subjective LLM pass is skipped entirely — the system spends
tokens on exactly the scripts most likely to pass.

---

## 9. Provider Abstraction

Every external capability is a `Protocol`; providers are chosen by configuration and composed:

```mermaid
flowchart LR
    subgraph LLM
      P[PrimaryProvider] --> F{Fallback?}
      F -- on error --> S[SecondaryProvider]
    end
    T[Tiering] -. selects .-> P
    P --- A[Anthropic]
    P --- O[OpenAI]
    P --- L[Local · OpenAI-compatible]
```

- **Fallback chain.** A primary provider failure (after `tenacity` retries) transparently fails over
  to a configured secondary — e.g. cloud → cloud, or cloud → local.
- **Tiered routing.** A *heavy* tier handles hard creative work (scriptwriting); a *light* tier
  handles mechanical, high-volume calls (JSON repair, discrete 1–5 judge scoring). Each tier maps to a
  configurable model, defaulting back to the generator/judge model.
- **Local-first.** `PRIMARY_PROVIDER=local` targets any OpenAI-compatible server (Ollama, LM Studio,
  vLLM, llama.cpp), making generation and judging free.

| Capability | Providers |
|------------|-----------|
| **LLM** | **Google Gemini** (best-first model fallback chain) · Anthropic · OpenAI · Local (OpenAI-compatible) |
| **TTS** | **IndexTTS-2** (free voice cloning + disentangled emotion, separate venv) · **Chatterbox** (free voice cloning, MIT) · **Edge** (free, online) · **Piper** (free, offline) · ElevenLabs · OpenAI |
| **Visuals & Graphics** | **Playwright 7-Template Motion-Graphics Engine** (1080p30 HTML/CSS/SVG → FFmpeg) · Google Image (best-first model chain, isolated `GOOGLE_IMAGE_API_KEY`) · OpenAI · Stability · Pollinations · Pexels/Pixabay/Coverr B-roll · matplotlib diagrams |
| **Render** | ffmpeg (`h264_nvenc`/`qsv`/`amf` → `libx264` auto-fallback, `loudnorm`, `atempo`, `zoompan`) · MoviePy (stub) · Avatar (stub) |
| **Publish** | YouTube Data API v3 (OAuth + read-only Data-API client for outlier mining & live title sync) |

---

## 10. Safeguards & Compliance

Three deterministic safeguards enforce the project's non-negotiable guarantees:

**Grounding** (`safeguards/grounding.py`). A statistic is any `$`, `%`, or multi-digit token. Each is
required to map to a scene `fact_ref` that exists in the `DataBrief`; unmapped stats are stripped
during generation and drive the Judge's grounding score.

**Source attribution** (`stamp_sources`). A hard rule: *a statistic is never shown without its exact
source*. For every scene whose narration still cites a (grounded) stat, the referenced fact's source
is stamped into the on-screen caption (`… · Source: Adzuna`). This runs **in code** after grounding
repair, so the guarantee holds even when the model omits it — and it is idempotent across re-runs.

**Disclosure** (`safeguards/disclosure.py`). `synthetic_disclosure` is always set and surfaced in the
description; a regex verifies the disclosure phrase. The publish path resolves to a hard gate:
`auto + public + no disclosure` can **never** resolve to a public upload — it downgrades to Private,
pending manual disclosure, every time.

The full chain is airtight: ungrounded stats are **stripped** → surviving stats have a **real fact**
→ that fact's **exact source is shown on screen** → nothing publishes public **without disclosure**.

---

## 11. Reliability & Error Handling

A single exception family (`ContentFoundryError`) lets any layer catch the whole hierarchy:

```
ContentFoundryError
├── ConfigError               # fail fast at startup
├── DataSourceError           # one source failed (recoverable)
├── NoDataError               # all sources failed
├── InsufficientDataError     # < MIN_FACTS grounded facts
├── LLMError                  # provider failure after retries + fallback
├── BudgetExhaustedError      # month-to-date spend hit the cap
├── SchemaValidationError     # artifact/JSON invalid
├── GroundingError · TTSError · RenderError · PublishError
```

**Layered policy:**

| Layer | Strategy |
|-------|----------|
| Network (sources, LLM, TTS, image, upload) | `tenacity` exponential backoff, then provider fallback |
| Data sources | Degrade gracefully — proceed on whatever succeeded, as long as ≥ `MIN_FACTS` |
| LLM output | Reformat-retry on bad JSON; type-coercion on malformed fields; strip on ungrounded stats |
| Render | Surface real ffmpeg stderr; optional avatar→ffmpeg fallback |
| Compliance | Fail closed — hard gate, never bypassed |

Failures are recoverable by construction: because each completed stage is persisted, an error at
stage *k* is fixed and the run resumes at *k*, never from the top.

---

## 12. Cost Control & Local-First Operation

Cost is a first-class configuration axis, tunable per stage:

1. **Local LLM** — `PRIMARY_PROVIDER=local` (Ollama); generation & judging free.
2. **Free voice** — `TTS_PROVIDER=edge` (online) or `piper` (offline), or `chatterbox` / `indextts` to clone your own voice (local, GPU-accelerated). *(TTS is the single largest paid cost otherwise.)*
3. **Free visuals** — procedural cards (`IMAGE_PROVIDER=none`) + a free Pexels key for real B-roll.
4. **Deterministic judge** — `JUDGE_MODE=deterministic` spends zero tokens.
5. **Budget cap** — `ENFORCE_BUDGET_CAP` hard-stops a run at the monthly budget.
6. **`--profile cheap`** — bundles the above into one flag.

| Configuration | Approx. cost / video |
|---------------|:--------------------:|
| Fully local (Ollama + Edge/Piper + cards) | **~$0** |
| Local LLM + cloud TTS | ~$0.10 |
| All paid cloud defaults | ~$2 |

---

## 13. Observability

- **Structured logging** via `structlog` — every stage emits machine-parseable events; log level and
  JSON/console format are configurable.
- **Live progress** — a Rich-based reporter renders a spinner and clean per-stage progress lines for
  interactive runs, backed by the same event stream as the logs.
- **Provenance** — every artifact records the producing code, model, and config hash.
- **Review dashboard** — a read-mostly Streamlit UI (`content-foundry dashboard`) to spot-check Judge
  reports, watch for generic-content drift, and approve drafts — the thin human layer that keeps a
  person out of the writing loop but in the approval loop.

---

## 14. Testing Strategy

| Metric | Value |
|--------|-------|
| Test files | 71 |
| Tests | **994 passing** |
| Coverage | **~86%** branch + line (gate: 85%) |
| Lint & hooks | `ruff` + `ruff-format` + `mypy` + `check_no_secrets.py` — all 4 pre-commit hooks green |
| Config hygiene | 295 `Settings` fields strictly synced across `.env`, `.env.example`, and `data/env.example` (0 drift, 0 dead fields) |

**Principles.** Tests are **hermetic** — an autouse fixture points the settings loader at a
non-existent dotenv and injects a controlled environment, so a developer's real `.env` can never leak
into a test. External providers are replaced by fakes; the canonical "good script" and "generic
script" fixtures anchor the Judge's behavior.

**Contract tests** lock in the guarantees, e.g.: a script citing an absent number → grounding
short-circuits to `REVISE` with **no** LLM call; a never-passing script stops at `MAX_REVISIONS` →
`FAILED`; a well-formed single-scene stub → rejected by the completeness gate with **zero** tokens; a
scene stating a stat → its source is stamped even when the model omits it.

---

## 15. Notable Engineering Decisions

A selection of decisions and hardening that shaped the current system:

- **Deterministic-first judging.** Moving six of ten rubric dimensions into plain code made the
  quality gate reproducible and nearly free, and enabled the token-saving short-circuit on scripts
  that are doomed to `REVISE`.

- **The completeness gate.** The original rubric scored *quality* but not *quantity*; a grounded
  23-word stub scored ~4/5 and passed (the hook dimension even *rewarded* brevity). A deterministic
  completeness gate — parameterized by `MIN_SCENES` and `MIN_SCRIPT_WORD_RATIO` — now rejects stubs
  before they reach production, closing a class of "technically valid but useless" output.

- **Source attribution as code, not prompt.** Requiring the model to cite sources is unreliable;
  guaranteeing it in a post-processing pass makes the rule unbreakable and idempotent.

- **Defensive coercion for small models.** Local models emit malformed JSON types (a `fact_ref` as a
  list, a stringified index). Rather than fail, the generator normalizes these — a pragmatic
  robustness layer that made the local-first path genuinely usable.

- **Environment-independent ffmpeg discovery.** Rendering auto-discovers ffmpeg across PATH and common
  install locations, sidestepping the frequent "installed but not on this shell's PATH" problem on
  Windows.

- **Call-time settings resolution.** Settings are resolved when first requested (not at import), which
  is what makes the hermetic test environment possible.

---

## 16. Limitations & Future Work

- **Per-scene source precision.** Grounding is scene-level (`SceneCue.fact_ref`); per-beat sub-references could further narrow multi-source scenes, though `collect_embedded_sources` already deduplicates every visually cited URL into the bottom `Sources:` block.
- **Cloned-voice caption alignment.** Offline zero-shot cloners (`IndexTTS-2`, `Chatterbox`) return audio without native word timestamps, so long-form relies on YouTube's synced auto-CC (`CAPTIONS_ENABLED=false` default) while Shorts burn estimated cues.
- **YouTube Studio-only controls.** End-screen cards, comment pinning, the Education "Type" metadata field, and the "Altered or synthetic content" disclosure checkbox are not exposed by the YouTube Data API v3; the pipeline emits `end_screen.json` and `package.md` checklists so Studio sign-off takes seconds.
- **6 GB VRAM ceiling for local TTS.** On a 6 GB laptop GPU, `IndexTTS-2` runs at ~3–4× realtime after evicting `qwen_emo` to CPU and casting `semantic_model` to `fp16`.

---

## 17. Technology Stack

| Concern | Libraries |
|---------|-----------|
| **Config & models** | `pydantic`, `pydantic-settings` |
| **LLM** | Google Gemini REST (`httpx`), `anthropic`, `openai` (+ any OpenAI-compatible local server) |
| **Data & search** | `httpx`, `ddgs` (DuckDuckGo) + Tavily/Brave REST, `lxml`, `python-dateutil` |
| **Persistence** | `SQLAlchemy` + SQLite |
| **CLI & scheduling** | `typer`, `rich`, `APScheduler` |
| **TTS & prosody** | `indextts` (IndexTTS-2, separate venv), `chatterbox-tts`, `edge-tts`, `piper-tts`, `elevenlabs`, `num2words` |
| **Visuals, motion graphics & audio** | `playwright` (headless Chromium 1080p templates), `matplotlib`, `Pillow`, `ffmpeg-python`, `pydub`, `rembg` |
| **Publishing** | `google-api-python-client`, `google-auth-oauthlib` |
| **Reliability & logging** | `tenacity`, `structlog` |
| **Dashboard** | `streamlit` |
| **Dev & security** | `pytest`, `pytest-cov`, `pytest-mock`, `respx`, `ruff`, `mypy`, `pre-commit`, `scripts/check_no_secrets.py` |

---

## 18. Appendix: Metrics & Glossary

**Codebase**

| | |
|---|---|
| Source modules | 100 files · ~26,600 LOC (+ 17 prompt files) |
| Tests | 71 files · 994 passing |
| Design spec | 26 chapters |
| Coverage / gate | ~86% branch+line / all 4 pre-commit hooks green |

**Glossary**

- **Artifact** — the versioned JSON (+ media) each stage writes; the unit of resumability.
- **run_id** — a short sequential id (e.g. `0006`) identifying a run; also its folder name under `output/runs/`.
- **Brief** — `data_brief.json`; the grounded facts a script must cite.
- **fact_ref** — a scene's index into `DataBrief.key_facts`; the basis of grounding and source attribution.
- **Verdict** — the Judge's decision: PASS / REVISE / FAIL.
- **Floor** — a per-dimension minimum that must hold regardless of the weighted total.
- **Completeness gate** — the deterministic scene-count/word-count check that rejects stub scripts.
- **Production gate** — the rule that only a PASS (or `--force`) may proceed to voiceover→publish.
- **Disclosure gate** — the rule that nothing goes public without the synthetic-content disclosure.
- **Dry run** — a full pipeline including a real mp4, but no upload.

---

*Content Foundry is specified in 26 chapters under `spec/` and documented for operators in
`Tutorial.md`. This report describes the system as implemented at version 1.0.0.*
1