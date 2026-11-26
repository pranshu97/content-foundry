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
│       │   ├── provenance.py      # Provenance block + utcnow()
│       │   ├── signals.py         # RawSignal, NormalizedSignal
│       │   ├── data_brief.py      # DataBrief + KeyFact (creator_supplied) + Citation
│       │   ├── ideas.py           # MinedIdea (proven outliers) + IdeaSelection (ideas.json)
│       │   ├── research.py        # ResearchBrief + ResearchPoint + InstructionPlan
│       │   ├── script.py          # Script + SceneCue (visual_source, source_url, is_comment_friction, ...)
│       │   ├── judge_report.py    # JudgeReport, DimensionScore, Verdict enum
│       │   ├── voiceover.py       # VoiceoverAsset + SceneAudioClip + WordTiming + SceneTiming
│       │   ├── visuals.py         # VisualPackage + SceneVisual + VisualShot (template_id/props/visual_meta)
│       │   ├── video.py           # VideoAsset (final mp4 + metadata)
│       │   ├── publish.py         # PublishResult (video id, privacy, disclosure)
│       │   └── run.py             # Run, Attempt, RunState enum
│       │
│       ├── providers/             # Vendor abstractions (LLM + media + publish)
│       │   ├── __init__.py
│       │   ├── base.py            # LLMProvider protocol, LLMResponse, extract_json
│       │   ├── google_provider.py # Gemini REST provider (best-first GOOGLE_MODELS chain)
│       │   ├── anthropic_provider.py
│       │   ├── openai_provider.py
│       │   ├── local_provider.py  # OpenAI-compatible local LLM (Ollama / LM Studio / vLLM)
│       │   ├── fallback.py        # FallbackProvider (primary→secondary)
│       │   ├── tiering.py         # TaskTier (HEAVY vs LIGHT) model selector
│       │   ├── tts.py             # TTSProvider + ElevenLabs/OpenAI/Edge/Piper + Chatterbox & IndexTTS-2
│       │   ├── indextts2_worker.py # persistent JSON line-protocol worker in separate venv
│       │   ├── text_normalize.py  # speechify_numbers, prepare_tts_text, acronym/designation rules
│       │   ├── image.py           # ImageProvider + Google/OpenAI/Stability/Pollinations + fallback
│       │   ├── broll.py           # Pexels/Pixabay/Coverr clients + cliche/relevance scoring
│       │   ├── sfx.py             # SfxLibrary (local data/sounds) + optional Freesound download
│       │   ├── render_backend.py  # RenderBackend + FfmpegBackend (loudnorm, zoompan, reframe)
│       │   ├── youtube.py         # Publisher protocol + YouTubePublisher + recategorized_snippet
│       │   └── youtube_data.py    # YouTube Data API v3 read-only client (outlier mining + live titles)
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
│       ├── agents/                # The seven core agents + specialist sub-agents
│       │   ├── __init__.py
│       │   ├── brainstorm.py      # Agent 0: topic brainstorming around --idea
│       │   ├── idea_miner.py      # Agent 0.5: proven-idea outlier mining (YouTube Data API)
│       │   ├── data_fetcher.py    # Agent 1 (orchestrates fetch + deterministic distill)
│       │   ├── distill.py         # deterministic KeyFact/angle extraction (no LLM)
│       │   ├── instruction_planner.py # Agent 1.4: atomic ask enumeration + routed brief plan
│       │   ├── research.py        # Agent 1.5: source-backed depth synthesis + low-tier domain filter
│       │   ├── script_generator.py# Agent 2 (script authoring, grounding repair, citation stamping)
│       │   ├── judge.py           # Agent 3 (10-dim quality gate; hybrid by default)
│       │   ├── judge_checks.py    # deterministic rubric & hard-gate checks
│       │   ├── voiceover.py       # Agent 4 (TTS narration + selective `--scene` re-voicing)
│       │   ├── pronunciation.py   # Agent 4.5: batched acronym letters-vs-word classifier
│       │   ├── visuals.py         # Agent 5 (3-tier storyboard orchestration + thumbnail + captions)
│       │   ├── broll_director.py  # Agent 5.5: whole-script B-roll search query director
│       │   ├── thumbnail_director.py # Agent 5.6: exemplar-driven thumbnail prompt + surgical refiner
│       │   ├── scene_image_director.py # Agent 5.7: dual-channel anti-collapse AI scene prompt engine
│       │   ├── storyboard_critic.py # Pre-render deterministic + LLM storyboard auditor & repair gate
│       │   ├── renderer.py        # Agent 6 (studio audio mix + timeline + FFmpeg encode)
│       │   └── publisher.py       # Agent 7 (YouTube upload + metadata + post-privacy comments)
│       │
│       ├── production/            # Render, motion-graphics & metadata helpers
│       │   ├── __init__.py
│       │   ├── motion_graphics.py # TemplateRegistry + 7 dark-mode animated 1080p HTML/CSS/SVG templates
│       │   ├── visual_router.py   # 3-tier router, override classifier, anchor controller & LLM prop author
│       │   ├── captions.py        # build .srt/.ass from word timings
│       │   ├── timeline.py        # map script scenes -> word-aligned timed media segments
│       │   ├── sound_design.py    # mix_studio_audio (scene SFX + UI ticks/whooshes + -30dB ambient bed)
│       │   ├── diagram.py         # draw a matrix / bars / ladder / flow shot with matplotlib
│       │   ├── motion.py          # pick + build a camera move for a generated still (zoompan)
│       │   ├── subscribe.py       # Subscribe-nudge badge (Pillow) + overlay spec
│       │   ├── like_nudge.py      # glowing Like badge (Pillow) + overlay spec
│       │   ├── end_screen.py      # watch-next recommendations + library_context guard
│       │   ├── affiliate.py       # curated + search-resolved affiliate resource links
│       │   ├── overlay.py         # avatar overlay spec
│       │   ├── seo.py             # title/chapters/hashtags + bottom `Sources:` provenance collector
│       │   └── timebox.py         # time-context for the writer (evergreen-by-default)
│       │
│       ├── prompts/               # Externalized XML-structured prompt library (17 .txt files)
│       │   ├── __init__.py        # loader: load_prompt(name) / render_prompt(...) -> str
│       │   ├── script_generator.system.txt
│       │   ├── script_wit_craft.system.txt
│       │   ├── judge.system.txt
│       │   ├── judge.rubric.txt
│       │   ├── brainstorm.system.txt
│       │   ├── instruction_asks.system.txt
│       │   ├── instruction_planner.system.txt
│       │   ├── research.system.txt
│       │   ├── affiliate_queries.system.txt
│       │   ├── pronunciation.system.txt
│       │   ├── broll_director.system.txt
│       │   ├── scene_image_director.system.txt
│       │   ├── scene_image_director_dual.system.txt
│       │   ├── motion_graphics_director.system.txt
│       │   ├── storyboard_critic.system.txt
│       │   ├── thumbnail_director.system.txt
│       │   └── thumbnail_refine.system.txt
│       │
│       ├── templates/             # The 6 structural templates (see Ch. 16)
│       │   ├── __init__.py        # TEMPLATES registry + selection logic
│       │   └── definitions.py     # Template dataclasses + beat sheets
│       │
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── orchestrator.py    # run state machine, resumability, revision loop
│       │   ├── creator_data.py    # `--data` loader + `--instructions` numeric fact extractor
│       │   ├── topic_relevance.py # anti-drift idea concept filtration
│       │   ├── package.py         # human-readable `package.md` builder
│       │   ├── stages.py          # STAGE order + start-from logic
│       │   └── artifacts.py       # load/save/validate JSON artifacts + `run_meta.json`
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
│   ├── seed_demo.py               # insert a demo run for the dashboard
│   ├── backup_secrets.py          # bundle/verify/restore local-only secrets & assets
│   ├── check_no_secrets.py        # pre-commit & CI guard blocking secrets/personal identifiers
│   ├── recategorize.py            # safe read-modify-write YouTube category backfill
│   └── verify_templates.py        # offline DOM-layout + ffprobe verifier for all 7 motion templates
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
