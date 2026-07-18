# Spec-First, Artifact-Driven, Doc-Maintained Development

A reusable playbook for building a project (with a coding agent or a team) the way this one was built.

**Core principle:** design fully in writing *before* code; make every stage a versioned artifact; keep a
small set of living documents that each have exactly one job; and treat a green test gate as the
definition of done.

---

## Phase 0 — Specify (design before any code)

Create a `spec/` folder as the **single source of truth**. Numbered chapters, each scoped *"detailed
enough to remove ambiguity, concise enough to stay actionable,"* written so an implementer (human or
agent) needs **no additional context**.

- `spec/README.md` = one-line summary + the end-to-end flow diagram + table of contents.
- Chapters (adapt to your domain):
  **Overview & Goals · System Architecture (+ layering rules) · Tech Stack & Dependencies ·
  File/Directory Structure · Data/DB Schema · Config & Env Vars · one chapter per component ·
  Interfaces (CLI/API/UI) · Error-Handling Strategy · Testing Strategy · Deployment · Sample Outputs ·
  Observability/Alerting.**
- Rule: **the spec is authoritative — don't invent fields or behavior beyond it.** When it's wrong, fix
  the spec first, then the code.

## Phase 1 — Plan the build (the bridge doc)

A `Reference.md` that front-loads the decisions the spec implies:

- **What we're building** — 1 line + the pipeline/flow.
- **Golden architectural rules (do not violate)** — import direction/layering (which layer may import
  which; leaf modules import nothing internal), the core invariant (ours: *"everything is an artifact —
  stages talk only via versioned files + a metadata DB"* = resumability), **determinism / cost
  discipline** (do the expensive or non-deterministic thing *only where it adds value*), and the
  **non-negotiables** (safety/compliance enforced in code, never soft).
- **Testability decision** — how tests stay fast & hermetic: lazy-import heavy SDKs *inside functions*,
  program to **protocols with fakes**, **no real network**, and the minimal deps needed just to run
  tests.
- **Artifacts & schemas** table (input → output per stage) + **must-have test contracts** (the handful
  of behaviors that MUST hold) + a **coverage floor**.
- **Build order** — strictly **bottom-up** so you never import something that doesn't exist yet.

## Phase 2 — Implement bottom-up

Build in dependency order, each module mirroring its spec chapter:

> leaf models → config/errors/logging → pure data (templates/prompts) →
> providers *(protocols + fakes, lazy SDK imports)* / adapters → persistence → components →
> orchestration → interfaces → tests.

**Definition of done for every step = the gate is green:** full suite passes + linter clean +
coverage ≥ floor. TDD the pure/deterministic logic; **smoke-test** the parts you can't unit-test (real
binaries/models) rather than faking them.

## Phase 3 — Iterate & maintain (the living-doc cadence)

A **small** set of docs, each with ONE job:

| Doc | Its one job | Update when |
|---|---|---|
| `spec/NN-*.md` | Authoritative design | Any behavior/schema/config change |
| `Reference.md` | Chronological **BUILD-LOG** + rules + learnings | Every feature batch → a dated `## N` section: *root cause → fix → decisions → the **absolute** test count* |
| `gotchas` (repo notes) | Traps, conventions, **regression insurance** ("don't do X because Y") | Every time you hit or narrowly avoid a mistake |
| `Future_Plans` | Backlog / scratchpad | Any new idea or defect |
| `Human_Tasks` | Manual setup the tooling **can't** do (keys, OAuth, installs, hardware) | Any new external dependency/credential |
| `README` / `Tutorial` / `.env.example` | User-facing | New feature, lever, or setting |

**"When a feature lands" checklist:** new source file → file-tree spec; new config → env spec +
`.env.example`; new capability → README/Tutorial bullet; **always** add a `Reference.md ## N` entry with
the current absolute test count; new credential/step → `Human_Tasks`.

---

## The disciplines that make it hold together

1. **Spec is the contract** — ambiguity resolved in writing, not improvised in code.
2. **Everything is a versioned artifact** → resumability, provenance, cheap single-stage re-runs.
3. **Determinism-first; expensive/non-deterministic only where it adds value** → cheaper, faster, more
   testable.
4. **Hermetic tests + a green gate = done** — fakes behind protocols, no network, a coverage floor
   that's non-negotiable.
5. **Non-negotiables enforced in code**, fail-closed (never "soft").
6. **Reference = long-term memory** — dated build-log with *absolute* (not delta) counts, so any
   decision can be reconstructed.
7. **gotchas = regression insurance** — every hard-won lesson written down so the next change can't
   silently undo it.
8. **Separate the human's job** — one checklist for everything the agent can't do.

---

## Code quality & engineering craft (non-negotiable throughout)

Quality is not a cleanup phase — it is upheld on **every** change.

- **Follow the language's idioms and style guide;** keep the linter/formatter/type-checker clean as
  part of the gate, not an afterthought.
- **Small, single-responsibility units;** clear names; **no dead code, no speculative abstractions.**
  Add a helper only when there's a second real caller.
- **Change only what the task needs.** Don't refactor, re-format, or "improve" unrelated code in the
  same edit — it hides the real change and invites regressions.
- **Validate at boundaries, trust the interior.** Don't add error handling for states that can't occur.
- **Comments explain *why*, not *what*.** The code says what; a comment earns its place by capturing a
  non-obvious reason, constraint, or trap.
- **Security by default:** no secrets in code, validate/escape external input, least-privilege
  credentials, and treat any external/LLM/tool output as untrusted.
- **Design for change:** depend on interfaces (protocols) not concretions, keep side effects at the
  edges, and make the expensive/irreversible actions explicit and opt-in.
- **Leave it greener:** every merge is lint-clean, type-clean, tested, and documented per the cadence
  above.

## Prompt engineering (when the system uses an LLM)

- **Treat prompts as versioned source.** Keep them in files/templates, review them like code, and note
  the invariants they must preserve.
- **Inject context dynamically, keep the shipped template generic.** Use named placeholders filled at
  runtime rather than hard-coding domain specifics into the prompt.
- **Be explicit and structured:** state the role, the task, hard rules, and the exact output format
  (prefer strict JSON with a schema you then validate). Give **worked examples** of the quality bar.
- **Constrain the failure modes you've seen:** name the exact bad behavior and forbid it (e.g., "never
  invent a number," "never drift off the chosen topic," "output only JSON").
- **Respect model limits:** token/context budgets (lead with what must survive truncation), reasoning
  vs. non-reasoning models (give reasoning models room so "thinking" doesn't crowd the answer), and
  provider quirks (system-role support, etc.).
- **Make prompt changes test-safe:** if tests use a fake LLM, ensure prompt edits can't change routing
  or fixtures; keep a note of any word/marker the test harness keys on.
- **Ground it:** give the model the real, relevant material (retrieved/searched for the *actual* task),
  and make facts serve the goal — never let a stray retrieved detail hijack the output.

## LLM-as-a-Judge (make evaluation effective, deterministic, unbiased, low-variance)

If you use an LLM to *score* or *gate* outputs, engineer the judge as carefully as the generator.

**Effective & well-scoped**
- **Deterministic-first:** compute everything you *can* in code (length, schema, keyword/grounding,
  duplication, compliance) and only ask the LLM for the genuinely subjective dimensions. Short-circuit:
  if a hard, cheap gate already fails, don't spend an LLM call.
- **Separate the rubric from the verdict.** The LLM emits per-dimension scores; deterministic code
  combines them into PASS/REVISE/FAIL with explicit floors and weights. Never let the model decide the
  gate directly.
- **Give it evidence, ask for justification.** Require the judge to cite the specific reason/quote for
  each score — it improves quality and gives you actionable revision feedback.

**Deterministic & low-variance**
- **Temperature 0** (or the lowest supported) for the judge; a fixed model/version; a fixed prompt.
- **Discrete, anchored scales** (e.g., integers 1–5 with a written anchor for *each* level) beat
  free-form or fine-grained 0–100 scoring — they're far more reproducible.
- **Mind coarse-scale math:** if you map a 1–5 score onto pass/fail thresholds, a floor that lands
  *between* achievable values secretly demands a perfect score. Set floors on values the scale can
  actually produce.
- **Structured output + validate + neutral default:** parse strict JSON; if a dimension is missing or
  malformed, default it to a neutral score rather than crashing or re-rolling.

**Unbiased & fair**
- **Anchor with a rubric, not vibes.** Explicitly tell it *not* to reward length, fluency, confidence,
  formatting, or effort — only the quality you care about. Modern models are always fluent; fluency
  must not lift the score.
- **Avoid the down-bias trap too.** "When unsure pick the lower score" + an over-harsh anchor makes a
  gate that *never* passes; calibrate so a genuinely good output *can* clear the bar.
- **Watch known LLM-judge biases:** position/order bias (randomize or fix the order of compared items),
  verbosity bias, self-preference (a model favoring its own style), and leniency drift.
- **Calibrate against ground truth:** keep a few known-good and known-bad fixtures; a good example must
  PASS and a weak one must FAIL, and those tests guard every future rubric/threshold change.

**Cost & loops**
- **Cache and reuse** judgments where inputs are unchanged; run the judge **once per attempt**, not per
  consumer.
- **Bound the revise loop** (max revisions) and **anchor on the best attempt so far** so one bad
  revision can't derail a near-miss. Optionally fail-fast a hopeless attempt.

---

## Reusable kickoff (for a new project)

1. Have the agent **write the full `spec/` first** (or write it yourself), ending with `spec/README.md`.
2. Seed `Reference.md` with the golden rules, testability decision, artifact table, must-have test
   contracts, and the bottom-up build order.
3. Instruct: *"Implement per spec, bottom-up, keep the gate green after every module, uphold the
   code-quality and prompt/judge principles, and maintain Reference/gotchas/Human_Tasks as you go."*
4. Thereafter, every change follows the **Phase-3 cadence** above.
