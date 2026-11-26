## 19. Output & Package Format Specification

### 19.1 Run directory layout
```text
output/runs/<run_id>/
├── run_meta.json              # persisted content_format, idea, --instructions, and --data
├── data_brief.json
├── ideas.json                 # brainstormed + outlier-mined ideas & chosen pick
├── instruction_plan.json      # Agent 1.4 routed atomic-ask plan (when --instructions used)
├── research.json              # Agent 1.5 source-backed depth points
├── script.json
├── judge_report.json
├── voiceover.json
├── visuals.json
├── shot_prompts.json          # per-shot {source, prompt, visual_type, template_id/props, visual_meta}
├── video.json
├── publish_result.json
├── end_screen.json            # recommended prior channel uploads for Studio end-cards
├── package.md                 # the human-facing deliverable (includes final description + Sources:)
├── run.log                    # structured JSON lines run log
└── assets/
    ├── narration.mp3           # joined preview voiceover
    ├── narration_mixed.wav     # lossless PCM studio mix (narration + SFX + -30dB ambient bed)
    ├── voiceover/              # per-scene raw TTS clips for selective `voiceover --scene N`
    ├── thumbnail.png
    ├── thumbnail_prompt.txt    # editable thumbnail image prompt (reused unless `--reset`)
    ├── pronunciations.json     # Agent 4.5 acronym verdicts stamped with RULES_VERSION
    ├── captions.srt
    ├── citations.srt           # top-strip burned citations (for non-graphic stat scenes)
    ├── like_badge.png
    ├── subscribe_badge.png
    ├── video.mp4
    └── scenes/
        ├── scene_0_shot_0.mp4  # 1080p30 Playwright motion graphic or stock B-roll clip
        └── scene_1_shot_2.png  # dual-channel editorial AI still
```

The reusable ones are the point of resuming: `assets/scenes/**` and `thumbnail_prompt.txt` mean a
re-voice or a re-render costs nothing in image credits. `pronunciations.json` is reused too, but only
while its `rules_version` matches the current spelling rules — see
[`10-agent-4-voiceover-tts.md`](10-agent-4-voiceover-tts.md#1031-agent-45--pronunciation-director-agentspronunciationpy).

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
assets/thumbnail.png — headline: "<thumbnail_text>"

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
