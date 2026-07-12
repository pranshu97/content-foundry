## 19. Output & Package Format Specification

### 19.1 Run directory layout
```text
output/runs/<run_id>/
├── data_brief.json
├── script.json
├── judge_report.json
├── voiceover.json
├── visuals.json
├── video.json
├── publish_result.json
├── package.md                 # the human-facing deliverable
└── assets/
    ├── narration.mp3
    ├── thumbnail.png
    ├── captions.srt
    ├── video.mp4
    └── scenes/
        ├── scene_0.png
        └── scene_1.mp4
```

### 19.2 Shared `provenance` block (every JSON artifact)
```json
{
  "produced_by": "script_generator",      // or "operator_edited"
  "model": "claude-sonnet-4-20250514",
  "config_hash": "sha256:...",             // secret-redacted config fingerprint
  "input_hashes": {"data_brief": "sha256:..."},
  "created_at": "2026-06-30T09:00:00Z",
  "schema_version": "1.0"
}
```
This is what makes runs auditable and reproducible, and how operator hand-edits are detected.

### 19.3 `package.md` (final deliverable)
A single Markdown file the operator opens to record/approve and publish. Skeleton:
```markdown
# <Chosen Title>

**Run:** <run_id>   **Template:** <template_id>   **Verdict:** PASS (4.2/5)
**YouTube:** <video_url> — status: **PRIVATE (draft)**

## ⚠️ MANDATORY DISCLOSURE CHECKLIST (do before going public)
- [ ] In YouTube Studio, set **"Altered or synthetic content" = Yes** (disclosure_set: <true|false>)
- [ ] Confirm thumbnail uploaded
- [ ] Confirm description includes the synthetic-content note
- [ ] Spot-check the Judge report for drift

## Title options
1. ...  2. ...  3. ...

## Description
<description draft, includes synthetic-content disclosure line>

## Tags
tag1, tag2, ...

## Thumbnail
assets/thumbnail.png — overlay: "<thumbnail_text>"

## Grounding (facts used)
- <fact statement> — <source>, <url>

## Script (recordable)
<scene-by-scene narration + on-screen text>
```

### 19.4 Compliance guarantees encoded in output
- `package.md` **always** renders the disclosure checklist; if `disclosure_set=false`, the checklist is shown as **blocking** and the video stays Private.
- The description draft always contains a synthetic-content disclosure sentence.
- `publish_result.json` records `disclosure_set` and `privacy_status` for audit.

### 19.5 Stability
- Artifact `schema_version` is checked on load; a bump requires a migration note. Filenames per stage are fixed (the orchestrator and dashboard rely on them).

---

---
[← Index](README.md) · [← Prev](18-scheduler.md) · [Next →](20-human-review-dashboard.md)
