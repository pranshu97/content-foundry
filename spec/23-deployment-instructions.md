## 23. Deployment Instructions

### 23.1 Prerequisites
- **Python 3.11+**
- **ffmpeg** on `PATH` — Windows: `winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`; Debian/Ubuntu: `sudo apt-get install -y ffmpeg`.
- API keys per [Ch. 3.7](03-technology-stack-dependencies.md#37-external-accounts--keys-required); Google OAuth `client_secrets.json` (Desktop app type) for YouTube.

### 23.2 Local setup
```bash
git clone <repo> && cd career-advice-channel
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                 # then fill in keys
mkdir -p secrets && cp /path/to/client_secrets.json secrets/
python scripts/init_db.py                            # create tables
career run --niche "tech careers" --to-stage judge   # smoke test (no upload)
```
First real `career publish` opens a browser for YouTube OAuth consent; the refresh token is cached to `YOUTUBE_TOKEN_FILE`.

### 23.3 Container deployment
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["career"]
CMD ["run"]
```
- Mount `.env`, `secrets/`, `data/`, and `output/` as volumes so creds, DB, and media persist.
- OAuth must be completed once interactively (run the consent flow locally, then mount the resulting `youtube_token.json`).

### 23.4 Scheduled / unattended operation
- **systemd:** a `career-scheduler.service` running `career schedule`, with `Restart=on-failure`.
- **Container:** run the scheduler image with `--restart unless-stopped`, or trigger `career run` from a managed cron (Cloud Scheduler / GitHub Actions on a schedule).
- Keep `PUBLISH_MODE=draft` in unattended mode so uploads stay Private until the operator approves in the dashboard.

### 23.5 Secrets & safety
- `.env`, `secrets/`, `data/`, `output/` are gitignored. Never bake secrets into the image.
- Use least-privilege OAuth scopes (`youtube.upload` + `youtube`).
- Rotate keys periodically; the redacted `config_hash` makes config changes auditable without leaking secrets.

### 23.6 Cost & quota notes
- **The only always-on LLM cost is Agent 2 (script generation).** Agent 1, template-fatigue, grounding, and compliance are deterministic; the Judge adds at most one small LLM call in `hybrid` mode (or zero in `deterministic`). Use `--profile cheap` (deterministic judge, no image gen) for iteration and `quality` for publishing.
- YouTube Data API: an upload costs ~1600 quota units (default 10k/day ≈ a handful of uploads). Monitor quota; the publisher surfaces 403-quota errors clearly.

### 23.7 Go-live checklist
- [ ] `ffmpeg` available; `career run --to-stage render` produces a valid mp4.
- [ ] OAuth consent completed; `--dry-run` publish works.
- [ ] Thresholds (`PASS_THRESHOLD`, `INSIGHT_MIN`, `GROUNDING_MIN`) tuned on a few sample runs.
- [ ] Disclosure gate verified (cannot go public without `disclosure_set`).
- [ ] Scheduler running with `PUBLISH_MODE=draft`.

---

---
[← Index](README.md) · [← Prev](22-testing-strategy.md) · [Next →](24-sample-output-examples.md)
