## 9. Judge Agent

### 9.1 Purpose
The quality gate. The Judge scores every script against a fixed rubric, enforces hard floors on **factual grounding** and the **Insight Score**, detects **template fatigue**, and returns one of three verdicts. Only a `PASS` unlocks the production stages (4–7).

**Cost-saving design:** the Judge is **deterministic-first**. Five of the seven rubric dimensions are computed by plain Python (no tokens). At most **one** cheap LLM call scores only the two genuinely subjective dimensions — and even that is optional (`JUDGE_MODE`). When the deterministic gates already decide the verdict (e.g., a grounding violation), the LLM call is **skipped entirely**.

### 9.2 Inputs / outputs
- **Input:** `Script` + `DataBrief` (to verify grounding) + **recent script summaries** (last `FATIGUE_LOOKBACK` runs, for fatigue detection) + current `attempt_number`.
- **Output:** `JudgeReport` artifact → `output/runs/<run_id>/judge_report.json`.

### 9.3 The full rubric
Each dimension is scored **0–10**. The **weighted total** is the publish-quality signal; dimensions with a **hard floor** can independently force a non-PASS even if the total is high.

| # | Dimension | Weight | Hard floor | Method | What a 10 looks like |
|---|-----------|:------:|:----------:|--------|----------------------|
| 1 | **Actionability** | 20% | — | LLM* / heuristic | Viewer can *do* something specific today; concrete steps, not vibes |
| 2 | **Specificity / Non-Generic** | 20% | — | **Deterministic** | Could not have been written without the data; names numbers, roles, tradeoffs |
| 3 | **Factual Grounding** | 20% | `GROUNDING_MIN` (8.0) | **Deterministic** | Every stat traces to a `DataBrief` fact; zero invented numbers |
| 4 | **Insight Score (value density)** | 20% | `INSIGHT_MIN` (7.0) | LLM* / heuristic | Contains a genuinely non-obvious insight that reframes the topic |
| 5 | **Hook & Retention** | 15% | — | **Deterministic** | First 10s create a curiosity gap; no slow throat-clearing |
| 6 | **Structural Freshness** | 10% | — | **Deterministic** | Opening, arc, and phrasing differ from recent videos |
| 7 | **Compliance (disclosure)** | 5% | pass/fail | **Deterministic** | `synthetic_disclosure=true` present and reflected in description |

*\* LLM-scored only when `JUDGE_MODE=hybrid|llm`; in `deterministic` mode a heuristic is used instead.*

> **Insight Score = value density.** It is its own gate (floor 7.0). Generic, "soul-crushing" advice scores low here and is rejected outright, even if everything else is fine — this is the core anti-mediocrity mechanism.

### 9.3a Deterministic checks (no tokens)
Implemented in `judge/checks.py`, run **before** any LLM call:
- **Grounding:** assert every number/`%`/`$` token in narration maps to a `Script.scenes[*].fact_ref` that exists in the `DataBrief`. Score = `10 * grounded_stats / total_stats`; below `GROUNDING_MIN` short-circuits to `REVISE`.
- **Compliance:** assert `synthetic_disclosure=true` and a disclosure phrase is present in `description` (regex). Pass/fail.
- **Structural Freshness / fatigue:** compare `template_id` and a hook-shingle (normalized 5-gram set, Jaccard) against the last `FATIGUE_LOOKBACK` runs from `template_usage`/stored hooks. Too-similar → `template_fatigue=true`.
- **Specificity:** ratio of "concrete" tokens (digits, `$`, `%`, capitalized role/tech terms) to total; mapped to 0–10.
- **Hook:** check the first scene contains a number/specific claim and is under N words; mapped to 0–10.
- **Generic-phrase penalty:** a blocklist (e.g. "network more", "update your resume", "work hard") deducts points and feeds the heuristic insight fallback.

### 9.3b `JUDGE_MODE`
- **`hybrid` (default):** deterministic checks + **one** LLM call scoring only Actionability & Insight (skipped if a hard gate already failed). ~80% cheaper than full-LLM.
- **`deterministic`:** zero LLM calls; Actionability & Insight come from the heuristics above. Free; recommended for high-volume/iteration.
- **`llm`:** original behavior — all seven dimensions scored by the LLM (highest fidelity, highest cost).

### 9.3c Eval-prompt techniques (LLM-as-a-Judge)
The optional LLM scoring pass (Actionability & Insight) follows evaluation best practices to stay calibrated and stable. These are baked into `judge.system.txt` / `judge.rubric.txt` ([Ch. 15](15-prompt-library.md#15-prompt-library)):
- **Discrete integer scale 1–5** (not free 0–10 floats or 0–1), with **every level explicitly anchored** to a description of what a 1/2/3/4/5 means. Code normalizes to the internal 0–10 scale via `score10 = (score_1_5 − 1) × 2.5` (1→0, 3→5, 5→10), so the Insight floor `INSIGHT_MIN=7.0` requires a **4 or 5**.
- **Reason-before-score (chain-of-thought):** the model writes a one-sentence justification **before** the integer, and must **quote ≥1 concrete span** from the script as evidence (combats hallucinated grading).
- **Bias mitigations stated explicitly in the prompt:**
  - *Recency / position bias* — evaluate the script as a whole; do not over-weight the first or last lines.
  - *Leniency / central-tendency bias* — use the full 1–5 range; reserve **5** for genuinely exceptional; default-skeptical.
  - *Verbosity bias* — length ≠ quality; long is not insightful.
  - *Self-preference bias* — grade against the rubric, not against "how an LLM would phrase it."
- **Independent dimensions:** Actionability and Insight are scored separately; one must not anchor the other.
- **Determinism:** temperature 0 ⇒ identical input yields identical score.

### 9.4 Template-fatigue detection
Computed **deterministically in code** (no LLM): the Judge loads the last `FATIGUE_LOOKBACK` runs' `template_id` and stored hooks from the DB. If the current script reuses a template back-to-back, or its hook shingle is too similar (Jaccard ≥ threshold), it sets `template_fatigue=true` and `force_shift=true`, names a **different** `forced_template_id`, and returns `REVISE`. This implements the "radical shift in structure or perspective" requirement at zero token cost.

### 9.5 Verdict logic
```text
compliance_failed         -> REVISE (or FAIL if attempts exhausted)
grounding < GROUNDING_MIN -> REVISE (ungrounded claims are non-negotiable)
insight  < INSIGHT_MIN    -> REVISE (too generic)
template_fatigue          -> REVISE + force_shift
weighted_total >= PASS_THRESHOLD AND all floors met AND not fatigued -> PASS
otherwise, if attempt_number >= MAX_REVISIONS                        -> FAIL
otherwise                                                            -> REVISE
```
On `REVISE`, the report includes **structured, actionable `revision_instructions`** the Generator consumes on the next attempt. On `FAIL`, the run halts and is surfaced to the operator (likely a data problem, not a writing problem).

### 9.6 `JudgeReport` schema (Pydantic)
```python
class DimensionScore(BaseModel):
    dimension: str
    score_1_5: int | None   # LLM-scored dims: discrete 1-5 (None for code-only dims)
    score: float            # normalized 0-10 (LLM: (score_1_5-1)*2.5; code dims: computed)
    weight: float
    minimum: float | None   # hard floor on the 0-10 scale
    passed: bool
    evidence: str | None    # quoted span(s) from the script (LLM-scored dims)
    justification: str
    fix_suggestion: str | None

class JudgeReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["judge_report"] = "judge_report"
    attempt_number: int
    template_id: str
    scores: list[DimensionScore]      # one per rubric dimension
    weighted_total: float             # 0-10
    insight_score: float              # surfaced for dashboards/floors
    grounding_score: float
    template_fatigue: bool
    force_shift: bool
    forced_template_id: str | None
    verdict: Verdict                  # PASS | REVISE | FAIL
    summary: str                      # 2-3 sentence human-readable verdict
    revision_instructions: str | None # consumed by Generator on REVISE
    provenance: Provenance
```

### 9.7 Reliability measures
- **Determinism:** the five code-scored dimensions are fully reproducible; the optional LLM pass runs at temperature 0 with the fixed rubric text from `judge.rubric.txt` ([Ch. 15](15-prompt-library.md#15-prompt-library)).
- **Short-circuiting:** if a deterministic hard gate (grounding/compliance) fails, the verdict is decided **without** calling the LLM — saving tokens on exactly the scripts most likely to be rejected.
- **Auditability:** every dimension carries a `justification` (the rule that fired, or the LLM rationale), persisted to `rubric_scores` for the dashboard and drift analysis.

### 9.8 Resumability hooks
- The operator can re-run **only** the Judge against a hand-edited `script.json` (e.g., to re-score after a manual rewrite).
- A `PASS` report is the signed key that the orchestrator checks before entering production — production cannot be started on a `REVISE`/`FAIL` script without an explicit `--force` override (logged).

---

---
[← Index](README.md) · [← Prev](08-agent-2-script-generator.md) · [Next →](10-agent-4-voiceover-tts.md)
