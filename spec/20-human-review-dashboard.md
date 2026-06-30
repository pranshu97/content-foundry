## 20. Human Review Dashboard

### 20.1 Purpose
A **read-mostly** Streamlit app (`dashboard/app.py`, launched via `content-foundry dashboard`) for the thin human layer: spot-check the Judge, watch for drift into generic territory, and give the final go-live OK. The operator is never in the writing loop.

### 20.2 Views
| View | Shows |
|------|-------|
| **Runs** | Sortable table: `run_id`, date, niche, template, verdict, weighted_total, insight_score, publish status |
| **Run detail** | Judge report (per-dimension scores + justifications), script preview, embedded `video.mp4`, thumbnail, grounding facts, disclosure status |
| **Drift / analytics** | Insight score over time, pass rate, template-usage distribution, fatigue-flag frequency |
| **Compliance** | Any runs with `disclosure_set=false` / `pending_manual_disclosure`, surfaced prominently |

### 20.3 The one write action: go-live approval
- For a Private/Unlisted draft, the operator can click **"Approve & publish public"**, which is **disabled** until a **"disclosure confirmed in Studio"** checkbox is ticked. Clicking calls the publisher to flip privacy to `public` and records `disclosure_set=true`.
- This is the only state-mutating action in the dashboard; everything else is observation.

### 20.4 Drift-spotting aids
- A trendline of `insight_score`; if the rolling average dips toward the floor, a banner warns "insight drift — review rubric/sources."
- Template-usage bars make over-reliance on one structure obvious at a glance (complements the Judge's automated fatigue check).
- A "recently FAILED" panel highlights systemic problems (often data-source gaps).

### 20.5 Implementation notes
- Reads exclusively from SQLite + the run directories; no business logic is duplicated here.
- Auth is out of scope (single operator, localhost). If exposed, put it behind a reverse proxy with basic auth.
- Secrets are never displayed.

---

---
[← Index](README.md) · [← Prev](19-output-package-format-specification.md) · [Next →](21-error-handling-strategy.md)
