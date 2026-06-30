## 1. Project Overview & Goals

### 1.1 What we are building
An **autonomous content factory** for a YouTube career-advice channel. The system turns raw labor-market data into polished, ready-to-record video scripts with almost zero day-to-day human involvement. It builds channel authority by consistently shipping *specific, non-obvious, data-grounded* career advice instead of recycled platitudes.

### 1.2 The core problem it solves
Generic career advice ("network more!", "update your resume!") destroys retention and risks demonetization. Manual research + scripting does not scale to a daily/weekly publishing cadence. This system scales output **without sacrificing insight density** by combining real data with a quality gate that actively rejects generic content.

### 1.3 The agent pipeline (high level)
**Core content agents (1–3)** produce a rubric-approved script; **production & publishing agents (4–7)** turn that script into a published video. The Judge (stage 3) is a hard quality gate — production never starts on a failing script.

| Stage | Agent | Responsibility | Output Artifact |
|-------|-------|----------------|-----------------|
| 1 | **Data Fetcher** | Pull real-time job postings, layoffs, salary & industry-report data; distill into a factual brief | `DataBrief` |
| 2 | **Script Generator** | Write a full video script using one of 6 rotating structural templates | `Script` |
| 3 | **Judge** | Score the script against a strict rubric; detect template fatigue; PASS / REVISE / FAIL (quality gate) | `JudgeReport` |
| 4 | **Voiceover (TTS)** | Synthesize narration audio from the approved script, with word-level timing | `VoiceoverAsset` |
| 5 | **Visuals** | Generate the thumbnail plus per-scene images / B-roll selections and a captions track | `VisualPackage` |
| 6 | **Video Renderer** | Assemble audio + visuals + captions into a final `.mp4` | `VideoAsset` |
| 7 | **YouTube Publisher** | Upload as a privacy-gated draft and set the synthetic-content disclosure | `PublishResult` |

A thin **Human Layer** spot-checks the Judge's reports and gives the final go-live OK on the uploaded *Private* draft — it is *not* in the writing/editing loop.

### 1.4 Primary goals (functional)
- **G1 — Grounded content:** Every script must cite at least one concrete, fetched data point (salary figure, layoff event, posting trend). No hallucinated statistics.
- **G2 — Insight enforcement:** A quantified **Insight Score** gates every script; below threshold ⇒ rejected/revised.
- **G3 — Anti-repetition:** The Judge detects "template fatigue" across recent runs and forces a structural/perspective shift.
- **G4 — Full resumability:** The operator can start the pipeline at *any* stage by supplying the previous stage's (optionally hand-edited) artifact. Stop after stage *k*, refine, resume at *k+1*.
- **G5 — Compliance by default:** Every output carries the mandatory "Altered or Synthetic Content" disclosure — set programmatically on upload where the API allows, and otherwise enforced by a hard manual gate before any video can go public.
- **G6 — End-to-end automation:** From data to a published (Private/Unlisted) YouTube draft in a single command — TTS narration, thumbnail, video render, and upload all automated, with every stage independently resumable.

### 1.5 Non-goals (explicitly out of scope)
- No fully hands-off **public** publishing by default — uploads land as **Private/Unlisted** drafts for a final human OK (configurable, but the synthetic-content disclosure is always enforced before public).
- No live-streaming, Shorts auto-cropping, or multilingual dubbing in v1 (the render backend is pluggable, so these are future add-ons).
- No automated comment / community-tab management or engagement bots.
- No multi-tenant / multi-user accounts — single operator assumed.
- No paid analytics integration or A/B publishing optimization (future work).

### 1.6 Success criteria
- A single command produces a compliant, rubric-passing script grounded in fresh data.
- ≥ 90% of auto-generated scripts pass the Judge on the first or second attempt.
- Operator time per published script ≤ 5 minutes (spot-check only).
- Zero published scripts containing fabricated statistics (enforced by grounding checks).

### 1.7 Key personas
- **Operator (you):** Runs the pipeline (manually or scheduled), reviews Judge reports, occasionally edits an intermediate artifact and resumes.
- **The agents:** Autonomous workers described above.

---

---
[← Index](README.md) · [Next →](02-system-architecture.md)
