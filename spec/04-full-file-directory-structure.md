## 4. Full File & Directory Structure

### 4.1 Tree
```text
career-advice-channel/
├── README.md
├── pyproject.toml                 # project metadata, ruff/mypy/pytest config
├── requirements.txt
├── .env.example                   # template for required env vars (no secrets)
├── .gitignore
├── .pre-commit-config.yaml
│
├── src/
│   └── content_foundry/
│       ├── __init__.py
│       ├── config.py              # pydantic-settings Settings object
│       ├── logging.py             # structlog setup, run-scoped binding
│       │
│       ├── models/                # Pydantic artifact + domain schemas
│       │   ├── __init__.py
│       │   ├── signals.py         # RawSignal, NormalizedSignal
│       │   ├── data_brief.py      # DataBrief artifact
│       │   ├── ideas.py           # MinedIdea (proven outliers) + IdeaSelection (ideas.json)
│       │   ├── script.py          # Script artifact (+ SceneCue: narration, b_roll shots, sfx, fact_ref)
│       │   ├── judge_report.py    # JudgeReport, RubricScore, Verdict enum
│       │   ├── voiceover.py       # VoiceoverAsset (audio path + word timings)
│       │   ├── visuals.py         # VisualPackage (thumbnail, scenes, captions) + VisualShot beats
│       │   ├── video.py           # VideoAsset (final mp4 + metadata)
│       │   ├── publish.py         # PublishResult (video id, privacy, disclosure)
│       │   └── run.py             # Run, Attempt, RunState enum
│       │
│       ├── providers/             # Vendor abstractions (LLM + media + publish)
│       │   ├── __init__.py
│       │   ├── base.py            # LLMProvider protocol, LLMResponse
│       │   ├── anthropic_provider.py
│       │   ├── openai_provider.py
│       │   ├── fallback.py        # FallbackProvider (primary→secondary)
│       │   ├── tts.py             # TTSProvider + ElevenLabs/OpenAI/Edge/Piper + Chatterbox voice-clone (CUDA)
│       │   ├── image.py           # ImageProvider + OpenAI/Stability impls
│       │   ├── broll.py           # Pexels + Pixabay stock-footage clients + MultiBrollClient
│       │   ├── sfx.py             # SfxLibrary (local data/sounds) + optional Freesound download
│       │   ├── render_backend.py  # RenderBackend + Ffmpeg/MoviePy/Avatar impls
│       │   ├── youtube.py         # Publisher protocol + YouTubePublisher (OAuth)
│       │   └── youtube_data.py    # YouTube Data API client (proven-idea outlier mining)
│       │
│       ├── datasources/           # Pluggable fetchers
│       │   ├── __init__.py
│       │   ├── base.py            # DataSource protocol
│       │   ├── adzuna.py          # job postings + salary
│       │   ├── layoffs.py         # layoffs RSS/dataset
│       │   ├── news.py            # industry reports / news
│       │   ├── bls.py             # optional gov baseline
│       │   ├── search.py          # domain-agnostic web search (DuckDuckGo/Tavily/Brave)
│       │   └── registry.py        # builds enabled sources from config
│       │
│       ├── agents/                # The seven agents (+ pre-pipeline idea discovery)
│       │   ├── __init__.py
│       │   ├── idea_miner.py      # Pre-pipeline: proven-idea outlier mining (YouTube Data API)
│       │   ├── data_fetcher.py    # Agent 1 (orchestrates fetch + deterministic distill)
│       │   ├── distill.py         # deterministic KeyFact/angle extraction (no LLM)
│       │   ├── script_generator.py# Agent 2 (the one always-on LLM call)
│       │   ├── judge.py           # Agent 3 (quality gate; hybrid by default)
│       │   ├── judge_checks.py    # deterministic rubric checks (grounding/compliance/fatigue/...)
│       │   ├── voiceover.py       # Agent 4 (TTS narration)
│       │   ├── visuals.py         # Agent 5 (deterministic prompts + thumbnail + captions)
│       │   ├── renderer.py        # Agent 6 (assemble final mp4)
│       │   └── publisher.py       # Agent 7 (YouTube upload)
│       │
│       ├── production/            # Render helpers (non-vendor logic)
│       │   ├── __init__.py
│       │   ├── captions.py        # build .srt/.ass from word timings
│       │   ├── timeline.py        # map script scenes -> timed media segments (per-beat clips)
│       │   ├── sound_design.py    # mix SFX cues onto the narration
│       │   ├── subscribe.py       # Subscribe-nudge badge (Pillow) + overlay spec
│       │   ├── overlay.py         # avatar overlay spec
│       │   ├── seo.py             # title/description/tag optimization
│       │   └── timebox.py         # time-context / year-stamping
│       │
│       ├── prompts/               # Exact prompt text (see Ch. 15)
│       │   ├── __init__.py        # loader: load_prompt(name) -> str
│       │   ├── script_generator.system.txt   # always used
│       │   ├── judge.system.txt   # used only in JUDGE_MODE=hybrid|llm
│       │   └── judge.rubric.txt
│       │   # (data_fetcher.system.txt & visuals.system.txt removed - now deterministic)
│       │
│       ├── templates/             # The 6 structural templates (see Ch. 16)
│       │   ├── __init__.py        # TEMPLATES registry + selection logic
│       │   └── definitions.py     # Template dataclasses + beat sheets
│       │
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── orchestrator.py    # run state machine, resumability, revision loop
│       │   ├── stages.py          # STAGE order + start-from logic
│       │   └── artifacts.py       # load/save/validate JSON artifacts
│       │
│       ├── persistence/
│       │   ├── __init__.py
│       │   ├── db.py              # SQLAlchemy engine/session
│       │   ├── schema.py          # ORM tables (mirror Ch. 5)
│       │   └── repository.py      # CRUD for runs/attempts/artifacts
│       │
│       ├── safeguards/
│       │   ├── __init__.py
│       │   ├── disclosure.py      # synthetic-content disclosure injector + publish gate
│       │   └── grounding.py       # checks every claim ties to a signal
│       │
│       ├── notifications/         # Alerting (see Ch. 25)
│       │   ├── __init__.py
│       │   ├── base.py            # Notifier protocol + NullNotifier
│       │   ├── telegram.py        # TelegramNotifier (Bot API)
│       │   ├── factory.py         # build notifier + NOTIFY_EVENTS filter
│       │   └── credit_monitor.py  # token/cost tracking -> low_credits alerts
│       │
│       ├── cli.py                 # Typer app (see Ch. 17)
│       └── scheduler.py           # APScheduler entry (see Ch. 18)
│
├── dashboard/
│   └── app.py                     # Streamlit review dashboard (see Ch. 20)
│
├── secrets/                       # gitignored: client_secrets.json, youtube_token.json, credentials.env (optional)
│
├── output/
│   └── runs/                      # <run_id>/ -> *.json artifacts, assets/ (audio,images,video), package.md
│
├── data/
│   ├── content_foundry.db           # SQLite (gitignored)
│   └── sounds/                     # bundled sound-effect clips for SFX (whoosh, ding, cash register, ...)
│
├── scripts/
│   ├── init_db.py                 # create tables
│   └── seed_demo.py               # insert a demo run for the dashboard
│
└── tests/
    ├── conftest.py                # fixtures: fake provider, fake sources, tmp run dir
    ├── unit/
    │   ├── test_models.py
    │   ├── test_datasources.py
    │   ├── test_data_fetcher.py
    │   ├── test_script_generator.py
    │   ├── test_judge.py
    │   ├── test_templates.py
    │   ├── test_safeguards.py
    │   └── test_artifacts.py
    ├── integration/
    │   ├── test_orchestrator_full.py
    │   ├── test_resume_from_stage.py
    │   └── test_revision_loop.py
    └── fixtures/
        ├── sample_signals.json
        ├── sample_data_brief.json
        ├── sample_script.json
        └── sample_judge_report.json
```

### 4.2 Layering rules (import direction)
`cli/scheduler/dashboard → pipeline → agents → (providers | datasources | templates | prompts | safeguards) → models`.
- `models` import nothing internal (leaf).
- `persistence` is used by `pipeline` only.
- No upward imports; no agent imports another agent. The Orchestrator is the only place that knows the full sequence.

### 4.3 Where artifacts live
- **JSON artifacts:** `output/runs/<run_id>/<stage>.json` (human-readable, hand-editable) — `ideas` (proven + brainstormed idea picks), `data_brief`, `script`, `judge_report`, `voiceover`, `visuals`, `video`, `publish_result`.
- **Media assets:** `output/runs/<run_id>/assets/` — `narration.mp3`, `thumbnail.png`, `scenes/`, `captions.srt`, `video.mp4`.
- **Metadata + index:** SQLite (`runs`, `attempts`, `artifacts`, `publish_results` tables) for querying and the dashboard.
- The final deliverable is the **uploaded YouTube draft** (Private/Unlisted) plus `output/runs/<run_id>/package.md` — script, title, description, tags, thumbnail reference, and the disclosure checklist.

---

---
[← Index](README.md) · [← Prev](03-technology-stack-dependencies.md) · [Next →](05-database-schema.md)
