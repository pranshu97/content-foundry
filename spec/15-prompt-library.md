## 15. Prompt Library

Prompts live as plain `.txt` files under `prompts/` and are loaded by `load_prompt(name)`. They use `{placeholder}` tokens filled at runtime. LLM stages **must request strict JSON** matching the relevant Pydantic schema. Each prompt is organized into **labeled XML-style sections** (`<role>`, `<template>`/`<grounding>`/`<sources>`, `<rules>`, `<output_format>`, …) so the model cleanly separates its instructions from the injected data; `render_prompt` fills only the named `{placeholder}` tokens and leaves every other brace (e.g. the JSON shape) untouched.

> **Cost & tiering note:** the Script Generator (§15.2) and Researcher use the `HEAVY` model tier; high-frequency or mechanical passes — the Judge, Instruction Planner, Pronunciation Director, B-Roll Director, Motion-Graphics Director, Dual-Channel Scene Image Director, Storyboard Critic, and Thumbnail Refiner — run on the cheap `LIGHT` tier (`temperature=0.0–0.35`). **FakeLLM invariant:** test harness `FakeLLMProvider` routes judge responses on `"judge" in system.lower()`, so **no non-judge prompt file may ever contain the word `"judge"`** (use `"reviewer"` / `"auditor"`).

### 15.1 Active prompt files (17)
| File | Component | Tier / When used |
|------|-----------|------------------|
| `script_generator.system.txt` | Agent 2 (`ScriptGenerator`) | `HEAVY` · every run |
| `script_wit_craft.system.txt` | Agent 2 appendix | `SCRIPT_WIT_CRAFT_ENABLED=true` |
| `judge.system.txt` + `judge.rubric.txt` | Agent 3 (`Judge`) | `LIGHT` (`temp=0`) · `JUDGE_MODE=hybrid\|llm` |
| `brainstorm.system.txt` | Agent 0 (`Brainstormer`) | `BRAINSTORM_ENABLED=true` (`--brainstorm`) |
| `instruction_asks.system.txt` + `instruction_planner.system.txt` | Agent 1.4 (`InstructionPlanner`) | `LIGHT` · when `--instructions` supplied |
| `research.system.txt` | Agent 1.5 (`Researcher`) | `HEAVY` · `RESEARCH_ENABLED=true` |
| `affiliate_queries.system.txt` | Orchestrator affiliate book search | `LIGHT` · `AFFILIATE_ENABLED=true` |
| `pronunciation.system.txt` | Agent 4.5 (`PronunciationDirector`) | `LIGHT` (`temp=0`) · `PRONUNCIATION_LLM_ENABLED=true` |
| `broll_director.system.txt` | Agent 5.5 (`BrollDirector`) | `LIGHT` · `BROLL_DIRECTOR_ENABLED=true` |
| `scene_image_director.system.txt` | Agent 5.7 legacy batch fallback | `LIGHT` · when `ENABLE_PROGRAMMATIC_GRAPHICS=false` |
| `scene_image_director_dual.system.txt` | Agent 5.7 (`compose_dual`) | `LIGHT` · dual-channel anti-collapse AI stills |
| `motion_graphics_director.system.txt` | `visual_router.author_motion_graphics_props` | `LIGHT` · batched props for the 7 dark-mode templates |
| `storyboard_critic.system.txt` | `StoryboardCritic` | `LIGHT` (`temp=0`) · pre-render storyboard audit |
| `thumbnail_director.system.txt` + `thumbnail_refine.system.txt` | Agent 5.6 (`ThumbnailDirector`) | `LIGHT` · draft + surgical find/replace touch-ups |

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
- GO DEEP / EXPLAIN THE MECHANISM (the Insight driver): never just state a tip — for each point unpack the claim, HOW it works under the hood, WHY it works (the cause, incentive, or psychology), a concrete example, and any non-obvious second-order effect. Every scene teaches something a smart viewer couldn't have guessed.
- WIT & PERSONALITY: the bar is a likable, human voice throughout PLUS a few lines that genuinely land (a vivid analogy that makes a mechanism click, a playful aside, a bit of self-aware humour) — NOT a forced joke in every scene, which lands flat and scores worse. Carries concrete dry-to-witty rewrite examples so the model matches the reviewer's "a little personality + an analogy that lands" bar. Humour rides on top of accurate substance, never replacing it.
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
NOT re-score them. Score these FOUR: Actionability, Insight, Engagement (does it grab and HOLD
attention?), and Wittiness (is it genuinely funny and fun to listen to?).

SCORING METHOD (follow exactly):
- Use a DISCRETE INTEGER 1-5 per the anchored level descriptions in the RUBRIC below.
- First write a one-sentence `justification`, THEN the integer (reason before scoring).
- Quote at least one concrete span from the script in `evidence`.
- Score the four dimensions INDEPENDENTLY.

HOW HARD TO GRADE (obey — be demanding, but reward the value a script genuinely earns):
- Anchor strictly to the rubric. Obvious or generic drafts sit at 2-3; a genuinely non-obvious,
  specific, data-backed point EARNS a 4; a 5 is rare, for the truly counterintuitive. Do not inflate,
  but do NOT withhold a 4 the script has clearly earned.
- Do NOT reward effort, confidence, length, or fluent writing on their own — only concrete, specific value.
- Merely restating a fact the audience could look up is a 3 on Insight; using that fact to land a
  non-obvious, useful point is a 4.
- "Do X" with no specifics (which tool, what number, what order) is a 3 on Actionability; 2-3 concrete,
  specific steps a viewer can start this week is a 4.
- Generic, "soul-crushing" advice ("network more", "update your resume", "stay positive") is a 1-2.
- Engagement: a flat, list-like recap that never builds curiosity is a 2-3; open loops, real stakes,
  pace changes, and direct address earn a 4. Wittiness: dry/corporate is 1-2, genuinely funny (a
  vivid analogy or a joke that lands) is a 4-5 — but only when the humour rides ON TOP of substance.

BIAS RULES (obey):
- Recency/position: judge the WHOLE script; do not over-weight the opening or ending.
- Verbosity: length is not quality. Self-preference: grade vs the rubric, not vs your own style.

RUBRIC:
{rubric_text}

SCRIPT:
{script_json}

Return ONLY valid JSON:
{ "actionability": {"justification": str, "evidence": str, "score_1_5": int},
  "insight":       {"justification": str, "evidence": str, "score_1_5": int},
  "engagement":    {"justification": str, "evidence": str, "score_1_5": int},
  "wittiness":     {"justification": str, "evidence": str, "score_1_5": int} }
```
The raw `score_1_5` is used **directly** on the 0-5 rubric scale (no rescaling). In `deterministic` mode this prompt is **not used** — all four dimensions fall back to heuristics ([Ch. 9.3b](09-judge-agent.md#9-judge-agent)).

### 15.4 `judge.rubric.txt` (anchored 1-5 scales for the LLM-scored dims)
```text
Score Actionability, Insight, Engagement, and Wittiness as DISCRETE INTEGERS 1-5 using these anchors.
Grade HARD, but reward what a script genuinely earns: obvious/flat drafts sit at 2-3, a
specific/non-obvious/lively one reaches 4.

ACTIONABILITY — can the viewer act on this today?
  1 = pure platitudes, nothing to do ("work hard", "stay positive").
  2 = vague direction, no concrete step.
  3 = one usable step, but generic or under-specified (no tool / number / order).
  4 = 2-3 concrete steps with real specifics (a named tool, number, or clear order) a viewer can
      start this week.
  5 = a precise, end-to-end playbook: ordered steps, specifics, and what to expect at each stage.

INSIGHT / VALUE DENSITY — is there a non-obvious idea? (FLOOR: needs >= 4)
  1 = cliché; everyone already knows this.
  2 = mildly useful but obvious.
  3 = one decent point a savvy viewer might not know, but essentially obvious or generic.
  4 = a genuinely non-obvious, useful point backed by a specific number or fact from the data.
  5 = a counterintuitive, memorable insight, provably backed by the data, that reframes the topic
      and changes what the viewer does next.

ENGAGEMENT / RETENTION — does it grab attention and hold it to the end?
  1 = dead air; no reason to keep watching.
  2 = flat and list-like; attention drifts.
  3 = watchable but even; few curiosity loops or turns.
  4 = pulls you forward: open loops, clear stakes/payoff, varied pace, talks to the viewer.
  5 = magnetic; every beat makes you need the next one, with no dead spots.

WITTINESS / ENTERTAINMENT — is it genuinely fun to listen to? (humour rides ON TOP of substance)
  1 = dry, corporate, zero personality.
  2 = one flat attempt at levity.
  3 = mild smile; a little personality but no real laugh.
  4 = genuinely funny: a vivid analogy, playful aside, or well-timed joke that lands.
  5 = consistently sharp and memorable, several real laughs, never at the cost of the facts.

Mapping: each 1-5 score is used DIRECTLY on the 0-5 scale (no rescaling).
The other SIX dimensions are computed deterministically in code (see Ch. 9.3a), on 0-10 internally
and halved to 0-5:
  Specificity (w=0.14), Factual Grounding (w=0.14, FLOOR=4.0), Hook & Retention (w=0.10),
  Structural Freshness (w=0.07), Compliance (w=0.03, PASS/FAIL), and Ending (w=0.07, FLOOR=3.0 —
  a word-match needing BOTH a like/subscribe nudge AND a sign-off). Actionability &
  Insight are w=0.14 each (Insight FLOOR=3.5); Engagement w=0.10 (no floor); Wittiness w=0.07 (FLOOR=2.5). Weights sum to 1.0.
weighted_total = sum(score * weight)  (a plain weighted average on 0-5). A dimension below its FLOOR forces non-PASS.
```

### 15.5 `visuals` — deterministic (no prompt)
The old `visuals.system.txt` LLM pass is **removed**. Per-scene image prompts and the
thumbnail prompt are now built by code from a fixed f-string template
(`VISUAL_STYLE` + `b_roll_keywords` + `on_screen_text`), and scene `kind` is chosen by a
simple rule. The per-scene image prompt also carries **quality cues** (cinematic lighting, sharp focus, high detail) and the thumbnail prompt is a **high-CTR template** (one bold focal subject, exaggerated emotion, dramatic rim lighting, rule-of-thirds composition, clean title space, no baked-in text/logos/real people). See [Ch. 11.5](11-agent-5-visuals-thumbnail.md#11-agent-5-visuals-thumbnail).

### 15.6 Conventions
- **JSON-only outputs** are validated against Pydantic; on parse failure the agent runs a single "reformat to valid JSON" retry before failing.
- Schema shapes (`{*_schema}`) are injected from the Pydantic models' JSON schema so prompts never drift from code.
- Temperatures: generator `LLM_TEMPERATURE`; judge `JUDGE_TEMPERATURE` (0.0, only in `hybrid`/`llm` mode). The fetcher and visuals stages make no LLM calls.

---

---
[← Index](README.md) · [← Prev](14-pipeline-orchestrator.md) · [Next →](16-template-definitions-all-6.md)
