## 17. CLI Interface

A `typer` app (`cli.py`, exposed as `career`). All commands are thin wrappers over the orchestrator. Global options: `--profile {cheap|quality}`, `--log-level`, `--dry-run`.

### 17.1 Commands
| Command | Purpose | Key options |
|---------|---------|-------------|
| `career run` | Run the full pipeline end-to-end | `--niche`, `--topic`, `--template`, `--from-stage`, `--to-stage`, `--input`, `--run-id`, `--force`, `--dry-run` |
| `career fetch` | Stage 1 only → `data_brief.json` | `--niche`, `--topic`, `--run-id` |
| `career generate` | Stage 2 only (needs a brief) | `--input data_brief.json`, `--template`, `--run-id` |
| `career judge` | Stage 3 only (needs a script) | `--input script.json`, `--run-id` |
| `career voiceover` | Stage 4 (needs approved script) | `--input script.json`, `--run-id` |
| `career visuals` | Stage 5 | `--run-id` (uses run artifacts) |
| `career render` | Stage 6 | `--run-id`, `--backend` |
| `career publish` | Stage 7 | `--run-id`, `--privacy`, `--mode {draft|auto}`, `--dry-run` |
| `career resume` | Continue a run from its next stage | `--run-id`, `--to-stage` |
| `career status` | Show a run's state, attempts, verdict | `--run-id` |
| `career list` | List recent runs (table) | `--limit`, `--state` |
| `career report` | Pretty-print the latest `JudgeReport` | `--run-id` |
| `career dashboard` | Launch the Streamlit dashboard | `--port` |
| `career init-db` | Create tables | — |
| `career config check` | Validate `.env`; print a **redacted** credential table (set ✓ / missing ✗) | `--profile` |
| `career notify-test` | Send a sample of each `NOTIFY_EVENTS` alert to verify the bot | — |
| `career schedule` | Start the APScheduler loop | `--cron` |

### 17.2 Resumability examples (the headline workflow)
```bash
# Full run, faceless default, upload as Private draft
career run --niche "tech careers" --topic "junior dev hiring"

# Stop after the brief, inspect/edit it, then resume from generation
career run --to-stage fetch --niche "tech careers"
#  ...edit output/runs/<run_id>/data_brief.json...
career run --run-id <run_id> --from-stage generate

# Stop after a PASS script; hand-edit the script; resume into production
career run --to-stage judge --niche "AI jobs"
#  ...edit script.json...
career run --run-id <run_id> --from-stage voiceover

# Re-render only (e.g., after swapping a scene image), then publish
career render  --run-id <run_id> --force
career publish --run-id <run_id> --mode draft

# Start from an externally written brief (skip Agent 1 entirely)
career generate --input my_brief.json --template myth_vs_reality
```

### 17.3 UX details
- `rich` tables for `list`/`status`; a colored verdict banner after `judge` (green PASS / yellow REVISE / red FAIL).
- Every command prints the `run_id` and the artifact path(s) it wrote.
- Stage commands validate the input artifact's `schema_version` and fail with a precise message if it is stale or malformed.
- `--dry-run` threads through to the publisher (and optionally the LLM via a canned-response provider) for safe end-to-end rehearsals.

---

---
[← Index](README.md) · [← Prev](16-template-definitions-all-6.md) · [Next →](18-scheduler.md)
