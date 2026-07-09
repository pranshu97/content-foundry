## 9. Judge Agent

### 9.1 Purpose
The quality gate. The Judge scores every script against a fixed rubric, enforces hard floors on **factual grounding**, **Insight**, **Wittiness**, and the **Ending**, detects **template fatigue**, and returns one of three verdicts. Only a `PASS` unlocks the production stages (4–7).

**Cost-saving design:** the Judge is **deterministic-first**. Six of the ten rubric dimensions are computed by plain Python (no tokens). At most **one** cheap LLM call scores only the four genuinely subjective dimensions — and even that is optional (`JUDGE_MODE`). When the deterministic gates already decide the verdict (e.g., a grounding violation), the LLM call is **skipped entirely**.

### 9.2 Inputs / outputs
- **Input:** `Script` + `DataBrief` (to verify grounding) + **recent script summaries** (last `FATIGUE_LOOKBACK` runs, for fatigue detection) + current `attempt_number`.
- **Output:** `JudgeReport` artifact → `output/runs/<run_id>/judge_report.json`.

### 9.3 The full rubric
Each dimension is scored **0–10**. The **weighted total** is the publish-quality signal; dimensions with a **hard floor** can independently force a non-PASS even if the total is high.

| # | Dimension | Weight | Hard floor | Method | What a 10 looks like |
|---|-----------|:------:|:----------:|--------|----------------------|
| 1 | **Actionability** | 14% | — | LLM* / heuristic | Viewer can *do* something specific today; concrete steps, not vibes |
| 2 | **Specificity / Non-Generic** | 14% | — | **Deterministic** | Could not have been written without the data; names numbers, roles, tradeoffs |
| 3 | **Factual Grounding** | 14% | `GROUNDING_MIN` (8.0) | **Deterministic** | Every stat traces to a `DataBrief` fact; zero invented numbers |
| 4 | **Insight Score (value density)** | 14% | `INSIGHT_MIN` (7.0) | LLM* / heuristic | Contains a genuinely non-obvious insight that reframes the topic |
| 5 | **Engagement / Retention** | 10% | — | LLM* / heuristic | Opens loops and holds attention start to finish; you can't look away |
| 6 | **Wittiness / Entertainment** | 7% | `WITTINESS_MIN` (5.0) | LLM* / heuristic | Genuinely funny and lively; the wit rides on top of the substance |
| 7 | **Hook & Retention** | 10% | — | **Deterministic** | First 10s create a curiosity gap; no slow throat-clearing |
| 8 | **Structural Freshness** | 7% | — | **Deterministic** | Opening, arc, and phrasing differ from recent videos |
| 9 | **Compliance (disclosure)** | 3% | pass/fail | **Deterministic** | `synthetic_disclosure=true` present and reflected in description |
| 10 | **Ending / Sign-off** | 7% | `ENDING_MIN` (6.0) | **Deterministic** | Closes with **both** a like/subscribe nudge AND a warm sign-off (each worth 5; word-match) |

*\* LLM-scored (Actionability, Insight, Engagement, Wittiness) only when `JUDGE_MODE=hybrid|llm`; in `deterministic` mode a heuristic is used instead. Weights **sum to 1.0**, so `weighted_total` is a plain weighted average of the ten 0–10 dimension scores (it stays on 0–10 — that is why `PASS_THRESHOLD` and the floors are 0–10 values). **Floors that force a non-PASS:** Grounding, Insight, Wittiness (≥5/10, i.e. a 3/5), and Ending; Engagement is a weighted contributor with no floor. Insight and Wittiness are relaxable by gate relief; Grounding, Compliance, Ending, and fatigue are not. Keep any subjective floor ≤ 7.5 — on the coarse 1-5→0-10 scale a higher floor would need a perfect 5.*

> **Insight Score = value density.** It is its own gate (floor 7.0). Generic, "soul-crushing" advice scores low here and is rejected outright, even if everything else is fine — this is the core anti-mediocrity mechanism.

### 9.3a Deterministic checks (no tokens)
Implemented in `judge/checks.py`, run **before** any LLM call:
- **Grounding:** assert every number/`%`/`$` token in narration maps to a `Script.scenes[*].fact_ref` that exists in the `DataBrief`. Score = `10 * grounded_stats / total_stats`; below `GROUNDING_MIN` short-circuits to `REVISE`.
- **Compliance:** assert `synthetic_disclosure=true` and a disclosure phrase is present in `description` (regex). Pass/fail.
- **Structural Freshness / fatigue:** compare `template_id` and a hook-shingle (normalized 5-gram set, Jaccard) against the last `FATIGUE_LOOKBACK` runs from `template_usage`/stored hooks. Too-similar → `template_fatigue=true`.
- **Specificity:** ratio of "concrete" tokens (digits, `$`, `%`, capitalized role/tech terms) to total; mapped to 0–10.
- **Hook:** check the first scene contains a number/specific claim and is under N words; mapped to 0–10.
- **Completeness (hard gate):** reject a draft too short to be a real video — `len(scenes) < MIN_SCENES` **or** `word_count < MIN_SCRIPT_WORD_RATIO × SCRIPT_TARGET_WORDS`. A single-scene stub short-circuits to `REVISE` with no LLM call. The rubric scores *quality*, not *quantity*, so without this gate a grounded but tiny stub scores well (a short hook even scores *higher*).
- **Redundancy / duplicate scenes (hard gate):** near-verbatim repeated scenes are lazy padding that drives viewers away, so any scene pair whose narration 3-gram Jaccard ≥ `MAX_SCENE_SIMILARITY` (0.5) short-circuits to `REVISE` with a note **naming the offending scene pairs** — stopping a model that recycles the same lines/facts across scenes.
- **Generic-phrase penalty:** a blocklist (e.g. "network more", "update your resume", "work hard") deducts points and feeds the heuristic insight fallback.

### 9.3b `JUDGE_MODE`
- **`hybrid` (default):** deterministic checks + **one** LLM call scoring the four subjective dimensions — Actionability, Insight, Engagement, Wittiness (skipped if a hard gate already failed). ~80% cheaper than full-LLM.
- **`deterministic`:** zero LLM calls; the four subjective dimensions come from the heuristics above. Free; recommended for high-volume/iteration.
- **`llm`:** always makes the subjective-scoring LLM call, even when a deterministic hard gate has already failed (maximum fidelity on the subjective dims, highest cost). The six deterministic dimensions are still code either way.

### 9.3c Eval-prompt techniques (LLM-as-a-Judge)
The optional LLM scoring pass (the four subjective dimensions) follows evaluation best practices to stay calibrated and stable. These are baked into `judge.system.txt` / `judge.rubric.txt` ([Ch. 15](15-prompt-library.md#15-prompt-library)):
- **Discrete integer scale 1–5** (not free 0–10 floats or 0–1), with **every level explicitly anchored** to a description of what a 1/2/3/4/5 means. Code normalizes to the internal 0–10 scale via `score10 = (score_1_5 − 1) × 2.5` (1→0, 3→5, **4→7.5**, 5→10), so the Insight floor `INSIGHT_MIN=7.0` requires a genuine **4**. Because the scale is coarse, keep `INSIGHT_MIN ≤ 7.5`: a floor set in (7.5, 10] is only clearable by a *perfect 5*, which silently makes the gate unreachable.
- **Reason-before-score (chain-of-thought):** the model writes a one-sentence justification **before** the integer, and must **quote ≥1 concrete span** from the script as evidence (combats hallucinated grading).
- **Bias mitigations stated explicitly in the prompt:**
  - *Recency / position bias* — evaluate the script as a whole; do not over-weight the first or last lines.
  - *Leniency / central-tendency bias* — grade **hard**: most drafts sit at 2–3, a **4** must be genuinely non-obvious, **5** is rare; when torn between two scores, pick the **lower**. Effort, confidence, length, and fluent writing earn nothing.
  - *Verbosity bias* — length ≠ quality; long is not insightful.
  - *Self-preference bias* — grade against the rubric, not against "how an LLM would phrase it."
- **Independent dimensions:** the subjective dimensions are scored separately; one must not anchor another.
- **Determinism:** temperature 0 ⇒ identical input yields identical score.

### 9.4 Template-fatigue detection
Computed **deterministically in code** (no LLM): the Judge loads the last `FATIGUE_LOOKBACK` runs' `template_id` and stored hooks from the DB. If the current script reuses a template back-to-back, or its hook shingle is too similar (Jaccard ≥ threshold), it sets `template_fatigue=true` and `force_shift=true`, names a **different** `forced_template_id`, and returns `REVISE`. This implements the "radical shift in structure or perspective" requirement at zero token cost.

### 9.5 Verdict logic
```text
compliance_failed         -> REVISE (or FAIL if attempts exhausted)
grounding < GROUNDING_MIN -> REVISE (ungrounded claims are non-negotiable)
insight  < INSIGHT_MIN    -> REVISE (too generic)
wittiness < WITTINESS_MIN -> REVISE (too dry — a 3/5 minimum)
ending   < ENDING_MIN     -> REVISE (abrupt close — needs BOTH a like/subscribe nudge AND a sign-off)
duplicate scenes          -> REVISE (near-verbatim repeats above MAX_SCENE_SIMILARITY; names the pairs)
incomplete (too short)    -> REVISE (fewer than MIN_SCENES or below the word floor)
template_fatigue          -> REVISE + force_shift
weighted_total >= PASS_THRESHOLD AND all floors met AND not fatigued AND complete -> PASS
otherwise, if attempt_number >= MAX_REVISIONS                        -> FAIL
otherwise                                                            -> REVISE
```
**Gate relief:** when `weighted_total >= GATE_RELIEF_SCORE`, the *insight*, *wittiness*, and *length* floors are relaxed by `GATE_RELIEF_RATIO` (grounding, compliance, ending, and fatigue are never relaxed) — so a near-miss on one soft floor doesn't block an otherwise-excellent draft; the report `summary` notes when relief was applied.

On `REVISE`, the report includes **structured, actionable `revision_instructions`** the Generator consumes on the next attempt: it leads with a **`KEEP INTACT`** list of the dimensions that already pass (so an edit does not regress them), then a per-shortfall critique that reuses the Judge's own reasoning + the evidence it flagged + a concrete fix. Paired with handing the Generator its previous draft to edit, this keeps the loop *converging* instead of trading one failing dimension for another. On `FAIL`, the run halts and is surfaced to the operator (likely a data problem, not a writing problem).

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
