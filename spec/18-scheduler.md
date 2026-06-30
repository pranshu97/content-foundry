## 18. Scheduler

### 18.1 Purpose
Run the pipeline automatically on a cadence (e.g., weekly) so the channel ships consistently with zero manual triggering — while still respecting the human-gated publish step.

### 18.2 Design
- `scheduler.py` uses **APScheduler** (`BlockingScheduler`) with a cron trigger from `SCHEDULE_CRON` (default `0 9 * * MON`).
- Each fire invokes `run_pipeline(from_stage="fetch", to_stage=<configurable>)`. **Default `to_stage="publish"` with `PUBLISH_MODE=draft`**, so videos are uploaded as **Private drafts** awaiting the operator's go-live — automation never publishes public content on its own.
- A **single-flight lock** (DB-backed flag or file lock) prevents overlapping runs.
- Each run is wrapped in try/except; failures are logged and (optionally) sent to a notifier (webhook/email) without crashing the scheduler.

### 18.3 Operational notes
```bash
content-foundry schedule                 # uses SCHEDULE_CRON
content-foundry schedule --cron "0 8 * * 1,4"   # twice weekly
```
- Recommended deployment: a `systemd` service or a container with restart policy (see [Ch. 23](23-deployment-instructions.md#23-deployment-instructions)).
- The scheduler records each fire in the `runs` table like any manual run, so the dashboard shows scheduled and manual runs together.
- For cloud cron (e.g., a managed scheduler) you can skip APScheduler and call `content-foundry run` directly — the CLI is the stable contract.

---

---
[← Index](README.md) · [← Prev](17-cli-interface.md) · [Next →](19-output-package-format-specification.md)
