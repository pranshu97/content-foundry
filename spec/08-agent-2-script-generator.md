## 8. Agent 2 — Script Generator

### 8.1 Purpose
Turn the `DataBrief` into a complete, recordable video **script** using one of six rotating structural templates ([Ch. 16](16-template-definitions-all-6.md)). The output is "systematically varied" — same factual grounding, deliberately different structure each time — and is pre-segmented into scenes so downstream production (voiceover, visuals, render) needs no further interpretation.

### 8.2 Inputs / outputs
- **Input:** `DataBrief` artifact + a chosen `template_id` (selected by the orchestrator, or forced by the Judge on a revision) + optional `judge_feedback` (on revisions).
- **Output:** `Script` artifact → `output/runs/<run_id>/script.json`.

### 8.3 Template selection (anti-fatigue)
1. The orchestrator queries `template_usage` for the last `FATIGUE_LOOKBACK` runs.
2. It picks the **least-recently-used** eligible template (weighted-random among the bottom half) so structure rotates naturally.
3. On a Judge-forced shift (`forced_shift=1`), the previously used template is **excluded** and a perspective modifier (e.g., contrarian, second-person, future-tense) is injected.

### 8.4 Generation flow
```mermaid
flowchart TD
    A[Load DataBrief + template beat sheet] --> B[Compose system+user prompt]
    B --> C[LLM call (temp=LLM_TEMPERATURE)]
    C --> D[Parse JSON -> Script model]
    D --> E[Grounding check: every stat maps to a KeyFact]
    E -->|ok| F[Inject disclosure metadata]
    E -->|ungrounded claim| G[Repair pass: strip/replace claim, retry <=2]
    F --> H[Persist artifact + provenance + template_usage row]
```

### 8.5 `Script` schema (Pydantic, production-aware)
```python
class SceneCue(BaseModel):
    index: int
    narration: str                 # exact words to be spoken (drives TTS)
    on_screen_text: str | None     # caption / lower-third / big-number callout
    b_roll_keywords: list[str]     # hints for Agent 5 (e.g. ["server room","layoff headline"])
    fact_ref: int | None           # index into DataBrief.key_facts if this scene cites data

class Script(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["script"] = "script"
    template_id: str
    title_options: list[str]       # 3-5 candidate titles (Judge/operator picks)
    hook: str                      # first 5-10 seconds, must be specific
    scenes: list[SceneCue]         # ordered narration segments
    cta: str                       # call to action (subscribe / next video)
    description: str               # YouTube description draft
    tags: list[str]
    thumbnail_concept: str         # visual idea + overlay text for Agent 5
    word_count: int
    grounded_fact_refs: list[int]  # which DataBrief facts were actually used
    synthetic_disclosure: bool = True
    provenance: Provenance
```

### 8.6 Generation rules (enforced in the prompt + post-checks)
- **Grounding:** Every quantitative claim must map to a `DataBrief.key_facts[i]` via `fact_ref`; ungrounded numbers are stripped in a repair pass.
- **Specificity:** The hook and at least 3 scenes must contain a concrete, non-obvious takeaway (no "network more" filler).
- **Length:** Target `SCRIPT_TARGET_WORDS` (±15%); narration is plain spoken English (no stage directions inside `narration`).
- **Disclosure:** `synthetic_disclosure=True` is always set and surfaced in the description draft.

### 8.7 Resumability hooks
- Emits a standalone `script.json` the operator can freely edit (rewrite the hook, swap a title) before resuming at the Judge.
- The Judge and all production stages consume **only** this artifact, so a hand-written script (matching the schema) can enter the pipeline at stage 3.

### 8.8 Failure modes
| Failure | Handling |
|---------|----------|
| Invalid JSON from LLM | Reformat-retry (≤2), then fail attempt |
| Ungrounded statistic | Repair pass; if unfixable, drop the claim and flag in provenance |
| Below word-count floor | One expansion retry, then pass to Judge (which will likely REVISE) |
| Template unavailable | Fall back to least-used template; log warning |

---

---
[← Index](README.md) · [← Prev](07-agent-1-data-fetcher.md) · [Next →](09-judge-agent.md)
