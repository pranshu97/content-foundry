## 15. Prompt Library

Prompts live as plain `.txt` files under `prompts/` and are loaded by `load_prompt(name)`. They use `{placeholder}` tokens filled at runtime. LLM stages **must request strict JSON** matching the relevant Pydantic schema.

> **Cost note:** only **one stage always calls an LLM** — the Script Generator (§15.2). The Judge calls an LLM **at most once** and only in `hybrid`/`llm` mode (§15.3). The **Data Fetcher** and **Visuals** stages are now fully deterministic and have **no prompts** (their old prompt files are removed; see [Ch. 7](07-agent-1-data-fetcher.md#7-agent-1-data-fetcher) and [Ch. 11](11-agent-5-visuals-thumbnail.md#11-agent-5-visuals-thumbnail)).

### 15.1 Active prompt files
| File | Stage | When used |
|------|-------|-----------|
| `script_generator.system.txt` | Agent 2 | every run (the one irreducible LLM call) |
| `judge.system.txt` + `judge.rubric.txt` | Judge | only `JUDGE_MODE=hybrid\|llm` |
| ~~`data_fetcher.system.txt`~~ | — | **removed** (deterministic extraction) |
| ~~`visuals.system.txt`~~ | — | **removed** (deterministic templating) |

### 15.2 `script_generator.system.txt`
```text
You are a top-tier career-advice scriptwriter. Write a {target_words}-word YouTube
script for the niche "{niche}" using the STRUCTURAL TEMPLATE below. The script must
feel specific and non-obvious — never generic ("network more", "fix your resume").

TEMPLATE: {template_name}
BEAT SHEET:
{template_beats}
{perspective_modifier}

GROUNDING (use these facts; cite each stat by its index as fact_ref):
{key_facts_json}

HARD RULES:
- Every quantitative claim MUST map to a provided fact via fact_ref. No other numbers.
- The hook (first ~10s) must open a curiosity gap with a concrete, specific claim.
- WIT & PERSONALITY (dialed way up): write like the viewer's funniest expert friend — punchy phrasing, vivid analogies, a real comedic beat in most scenes — with the humour always riding on top of accurate substance, never replacing it.
- STICK THE LANDING: the final scene must pay off the core idea with the wittiest line, give one natural like/subscribe nudge, and sign off warmly, all inside that scene's spoken narration.
- Each scene.narration is plain spoken English (no stage directions in narration).
- Provide on_screen_text and b_roll_keywords per scene for downstream production.
- Set synthetic_disclosure=true and reflect it in the description draft.
{revision_clause}

Return ONLY valid JSON matching this shape:
{script_schema}
```

### 15.3 `judge.system.txt` (subjective dimensions only)
```text
You are a HARSH, adversarial content-quality judge for a career-advice channel. Your default stance
is skeptical: assume a script is mediocre until it clearly proves otherwise. Grounding, compliance,
specificity, hook, and template-freshness have ALREADY been scored deterministically by code — do
NOT re-score them. Score ONLY: Actionability and Insight.

SCORING METHOD (follow exactly):
- Use a DISCRETE INTEGER 1-5 per the anchored level descriptions in the RUBRIC below.
- First write a one-sentence `justification`, THEN the integer (reason before scoring).
- Quote at least one concrete span from the script in `evidence`.
- Score the two dimensions INDEPENDENTLY.

HOW HARD TO GRADE (obey — this pipeline over-rewards itself, so correct for it):
- MOST drafts land at 2 or 3. A 4 must be genuinely non-obvious or a precise, specific playbook; a 5
  is rare and reserved for the exceptional. If you find yourself giving mostly 4s and 5s, you are
  grading too soft — lower them.
- When you hesitate between two scores, choose the LOWER one.
- Do NOT reward effort, confidence, length, or fluent writing — only concrete, specific value.
- Accurately restating facts the audience could look up is at most a 3 on Insight, never higher.
- "Do X" with no specifics (which tool, what number, what order) is at most a 3 on Actionability.
- Generic, "soul-crushing" advice ("network more", "update your resume", "stay positive") is a 1-2.

BIAS RULES (obey):
- Recency/position: judge the WHOLE script; do not over-weight the opening or ending.
- Verbosity: length is not quality. Self-preference: grade vs the rubric, not vs your own style.

RUBRIC:
{rubric_text}

SCRIPT:
{script_json}

Return ONLY valid JSON:
{ "actionability": {"justification": str, "evidence": str, "score_1_5": int},
  "insight":       {"justification": str, "evidence": str, "score_1_5": int} }
```
Code maps `score_1_5` → 0-10 via `(score_1_5-1)*2.5`. In `deterministic` mode this prompt is **not used** — both dimensions fall back to heuristics ([Ch. 9.3b](09-judge-agent.md#9-judge-agent)).

### 15.4 `judge.rubric.txt` (anchored 1-5 scales for the LLM-scored dims)
```text
Score Actionability and Insight as DISCRETE INTEGERS 1-5 using these anchors. Grade HARD: the top
scores must be earned, and most scripts sit at 2-3.

ACTIONABILITY — can the viewer act on this today?
  1 = pure platitudes, nothing to do ("work hard", "stay positive").
  2 = vague direction, no concrete step.
  3 = one usable step, but generic or under-specified (no tool / number / order).
  4 = 2-3 concrete, SEQUENCED steps with real specifics (named tools, numbers, thresholds).
  5 = a precise, end-to-end playbook: ordered steps, specifics, and what to expect at each stage.

INSIGHT / VALUE DENSITY — is there a non-obvious, reframing idea? (FLOOR: needs >= 4)
  1 = cliché; everyone already knows this.
  2 = mildly useful but obvious.
  3 = one decent point a savvy viewer might not know, but not a reframe.
  4 = a genuinely non-obvious insight that reframes the topic AND is tied to a specific number/fact.
  5 = a counterintuitive, memorable insight, provably backed by the data, that changes what the
      viewer does next.

Mapping to the weighted rubric: score10 = (score_1_5 - 1) * 2.5  (1->0 ... 5->10).
The other five dimensions are computed deterministically in code (see Ch. 9.3a):
  Specificity (w=0.20), Factual Grounding (w=0.20, FLOOR=8.0),
  Hook & Retention (w=0.15), Structural Freshness (w=0.10),
  Compliance (w=0.05, PASS/FAIL). Actionability & Insight are w=0.20 each.
weighted_total = sum(score10 * weight). A dimension below its FLOOR forces non-PASS.
```

### 15.5 `visuals` — deterministic (no prompt)
The old `visuals.system.txt` LLM pass is **removed**. Per-scene image prompts and the
thumbnail prompt are now built by code from a fixed f-string template
(`VISUAL_STYLE` + `b_roll_keywords` + `on_screen_text`), and scene `kind` is chosen by a
simple rule. See [Ch. 11.5](11-agent-5-visuals-thumbnail.md#11-agent-5-visuals-thumbnail).

### 15.6 Conventions
- **JSON-only outputs** are validated against Pydantic; on parse failure the agent runs a single "reformat to valid JSON" retry before failing.
- Schema shapes (`{*_schema}`) are injected from the Pydantic models' JSON schema so prompts never drift from code.
- Temperatures: generator `LLM_TEMPERATURE`; judge `JUDGE_TEMPERATURE` (0.0, only in `hybrid`/`llm` mode). The fetcher and visuals stages make no LLM calls.

---

---
[← Index](README.md) · [← Prev](14-pipeline-orchestrator.md) · [Next →](16-template-definitions-all-6.md)
