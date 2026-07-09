## 22. Testing Strategy

### 22.1 Philosophy
Deterministic, **no real network/API calls**. All vendors are mocked behind their protocols; LLM outputs are canned JSON. The pipeline's artifact-passing design makes every stage independently testable.

### 22.2 Test pyramid
| Level | Scope | Examples |
|-------|-------|----------|
| **Unit** | One module, mocked deps | models validate/reject; template selection; safeguards; artifact load/save + hashing |
| **Agent** | One agent w/ fake provider/sources | fetcher drops uncited facts; generator strips ungrounded stats; judge applies floors & fatigue |
| **Integration** | Orchestrator + fakes | full run; revision loop; resume-from-stage; production gate |
| **E2E (dry-run)** | CLI → `DryRunPublisher` | `content-foundry run --dry-run` produces a complete package + `publish_result` |

### 22.3 Core fixtures (`conftest.py`)
- `FakeLLMProvider` — returns scripted, schema-valid JSON per stage (and a "bad JSON then good JSON" variant to test reformat-retry).
- `FakeDataSource` — yields fixture signals; a `FlakyDataSource` raises to test graceful degradation.
- `FakeTTS` / `FakeImageProvider` / `FakeRenderBackend` — emit tiny placeholder media + deterministic timings.
- `DryRunPublisher` — records intended calls, never hits YouTube.
- `tmp_run_dir` — isolated `output/runs/<run_id>/` per test; in-memory SQLite.

### 22.4 Must-have test cases (the contracts)
1. **Grounding (deterministic):** a script citing a number absent from the brief → grounding check < `GROUNDING_MIN` → `REVISE`, **with no LLM call** (short-circuit).
2. **Insight floor:** generic script → `insight_score < INSIGHT_MIN` → `REVISE`.
3. **Template fatigue (deterministic):** same template as last run → `force_shift=true` + different `forced_template_id`, computed from the DB without an LLM.
4. **Revision loop bound:** never-passing script stops at `MAX_REVISIONS` → `FAILED`.
5. **Resumability:** run `--to-stage judge`, mutate `script.json`, run `--from-stage voiceover` → uses edited script; provenance flips to `operator_edited`.
6. **Disclosure gate (critical):** `PUBLISH_MODE=auto`, `privacy=public`, `disclosure_set=false` → publisher refuses public, stays Private. Assert it can **never** go public without disclosure.
7. **Schema versioning:** loading a stale `schema_version` raises `SchemaValidationError`.
8. **Cache TTL:** signals within TTL are reused; expired ones refetched.
9. **Deterministic distill (no LLM):** Agent 1 builds the `DataBrief` from fixture signals with the `FakeLLMProvider` asserting **zero calls**; every `KeyFact.value` equals a source signal field.
10. **`JUDGE_MODE=deterministic` makes zero LLM calls:** assert `FakeLLMProvider` call count == 0 across a full judge pass; heuristics fill the four subjective dimensions.
11. **Visuals prompts are deterministic:** given a fixed `SceneCue`, the built image prompt is a pure function of inputs (no LLM call).

### 22.5 HTTP & tooling
- `respx` mocks Adzuna/NewsAPI/Pexels/YouTube HTTP.
- `pytest-cov` with a **≥85%** line-coverage gate on `src/content_foundry` (excluding vendor adapters' thin glue).
- `ruff` + `mypy` run in CI; `pre-commit` enforces format/lint before commits.
- A `make test` / `nox` target runs lint + type + tests.

---

---
[← Index](README.md) · [← Prev](21-error-handling-strategy.md) · [Next →](23-deployment-instructions.md)
