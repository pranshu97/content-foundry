## 21. Error Handling Strategy

### 21.1 Exception hierarchy
```python
class ContentFoundryError(Exception): ...            # base
class ConfigError(ContentFoundryError): ...          # bad/missing settings (fail fast at startup)
class DataSourceError(ContentFoundryError): ...       # one source failed (recoverable)
class NoDataError(ContentFoundryError): ...           # all sources failed
class InsufficientDataError(ContentFoundryError): ... # < MIN_FACTS grounded facts
class LLMError(ContentFoundryError): ...              # provider failure after retries
class SchemaValidationError(ContentFoundryError): ... # artifact/JSON invalid
class GroundingError(ContentFoundryError): ...        # ungrounded claim couldn't be repaired
class RenderError(ContentFoundryError): ...           # ffmpeg/backend failure
class PublishError(ContentFoundryError): ...          # upload/auth/quota failure
```

### 21.2 Layered policy
| Layer | Strategy |
|-------|----------|
| **Network (sources, LLM, TTS, image, upload)** | `tenacity` exponential backoff (3 tries); then provider fallback where one exists |
| **Provider** | Primary → fallback (LLM, TTS); render → `FfmpegBackend` if `RENDER_FALLBACK` |
| **Data sources** | Degrade gracefully: skip a failed source, note in `gaps[]`; only `NoDataError` halts |
| **Validation (boundaries)** | Validate artifacts on load/save; one reformat-retry for LLM JSON, then fail the stage |
| **Stage** | On unrecoverable error, mark `runs.state=FAILED`, persist partial artifacts, exit non-zero |

### 21.3 Fail-soft vs. fail-hard
- **Fail-soft** (continue with a note): a single dead source, no Pexels match, missing word timings (even-split fallback), avatar timeout → ffmpeg.
- **Fail-hard** (stop the run): config errors, no data at all, repeated invalid LLM JSON, unrepairable grounding violation, `ffmpeg` missing, auth/quota failures.
- **Compliance never fails soft:** an unconfirmed disclosure forces Private + manual gate — it never silently proceeds.

### 21.4 Resumability as recovery
Because every completed stage persisted a validated artifact, a failure is never catastrophic: fix the cause (add a source, edit the brief, install ffmpeg) and resume with `content-foundry resume --run-id <id>`. No work is redone unless `--force`.

### 21.5 Observability
- `structlog` JSON logs bound with `run_id`, `attempt_number`, `stage`; one line per stage transition with timing and token/cost estimates.
- Errors log the exception type, the stage, and the artifact path for fast triage.
- Exit codes: `0` success, `1` recoverable stop (resumable), `2` config/startup error.
- **Alerting:** unrecoverable failures emit a `run_failed` notification, and exhausted credit/quota emits `low_credits` ([Ch. 25](25-notifications-alerting.md#25-notifications--alerting)). Alerting is best-effort and never changes the exit path.

---

---
[← Index](README.md) · [← Prev](20-human-review-dashboard.md) · [Next →](22-testing-strategy.md)
