## 24. Sample Output Examples

A coherent walkthrough for one run (`niche="tech careers"`, `topic="junior developer hiring"`). Values are illustrative but schema-accurate.

### 24.1 `data_brief.json` (excerpt)
```json
{
  "schema_version": "1.0",
  "run_id": "01J9X2...",
  "stage": "data_brief",
  "niche": "tech careers",
  "topic_seed": "junior developer hiring",
  "key_facts": [
    {
      "statement": "Entry-level software postings fell 31% YoY while senior postings rose 4%.",
      "metric": "posting_volume_yoy",
      "value": "-31%",
      "citation": {"source": "adzuna", "url": "https://...", "observed_at": "2026-06-28T00:00:00Z",
                   "snippet": "junior software engineer postings -31% YoY; senior +4%"}
    }
  ],
  "content_angles": [
    {"hook": "The 'learn to code' ladder lost its bottom rung — here's the new entry point.",
     "supporting_fact_ids": [0], "why_nonobvious": "Most advice still assumes junior roles are the entry path."}
  ],
  "coverage": {"adzuna": true, "layoffs": true, "news": true, "bls": false},
  "gaps": ["bls: series timeout"]
}
```

### 24.2 `script.json` (excerpt)
```json
{
  "schema_version": "1.0", "run_id": "01J9X2...", "stage": "script",
  "template_id": "contrarian",
  "title_options": ["The Junior Dev Job Is Disappearing (Do This Instead)",
                    "Why 'Get an Entry-Level Coding Job' Is 2026's Worst Advice"],
  "hook": "Entry-level coding postings just dropped 31% in a year — the bottom rung of the ladder is gone.",
  "scenes": [
    {"index": 0, "narration": "Everyone still says 'just get a junior dev job.' The data says that door is closing fast.",
     "on_screen_text": "Junior postings -31% YoY", "b_roll_keywords": ["closed door","job board"], "fact_ref": 0}
  ],
  "cta": "Subscribe for the data-backed moves the generic channels won't tell you.",
  "description": "Grounded in Adzuna posting data (Jun 2026). Note: this video uses AI-altered/synthetic content.",
  "tags": ["tech careers","junior developer","2026 job market"],
  "thumbnail_concept": "Broken ladder with missing bottom rung; bold text 'BOTTOM RUNG GONE'",
  "word_count": 910, "grounded_fact_refs": [0], "synthetic_disclosure": true
}
```

### 24.3 `judge_report.json` (excerpt — a PASS)
```json
{
  "schema_version": "1.0", "run_id": "01J9X2...", "stage": "judge_report",
  "attempt_number": 1, "template_id": "contrarian",
  "scores": [
    {"dimension": "actionability", "score": 8, "weight": 0.20, "minimum": null, "passed": true,
     "justification": "Gives a concrete alternative entry path.", "fix_suggestion": null},
    {"dimension": "grounding", "score": 9, "weight": 0.20, "minimum": 8.0, "passed": true,
     "justification": "All stats trace to fact_ref 0.", "fix_suggestion": null},
    {"dimension": "insight", "score": 8, "weight": 0.20, "minimum": 7.0, "passed": true,
     "justification": "Reframes the entry path; non-obvious.", "fix_suggestion": null}
  ],
  "weighted_total": 8.4, "insight_score": 8.0, "grounding_score": 9.0,
  "template_fatigue": false, "force_shift": false, "forced_template_id": null,
  "verdict": "PASS",
  "summary": "Specific, well-grounded contrarian take with a clear action. Approved for production.",
  "revision_instructions": null
}
```

### 24.4 `publish_result.json` (excerpt)
```json
{
  "schema_version": "1.0", "run_id": "01J9X2...", "stage": "publish",
  "youtube_video_id": "dQw4...", "video_url": "https://youtu.be/dQw4...",
  "privacy_status": "private", "disclosure_set": false,
  "upload_status": "pending_manual_disclosure",
  "chosen_title": "The Junior Dev Job Is Disappearing (Do This Instead)",
  "published_at": null
}
```

### 24.5 `package.md` (excerpt)
```markdown
# The Junior Dev Job Is Disappearing (Do This Instead)
**Run:** 01J9X2...  **Template:** contrarian  **Verdict:** PASS (8.4/10)
**YouTube:** https://youtu.be/dQw4... — status: **PRIVATE (draft)**

## ⚠️ MANDATORY DISCLOSURE CHECKLIST
- [ ] Set "Altered or synthetic content" = Yes in Studio  (disclosure_set: false)
- [ ] Confirm thumbnail + synthetic-content note in description
```

### 24.6 What "good" looks like
- The brief has **cited** facts and explicit `gaps`. The script's every number has a `fact_ref`. The Judge clears both hard floors and explains each score. The publisher left the video **Private** with a **blocking** disclosure checklist — exactly the safeguard behavior required.

---

*End of specification. This document is the single source of truth for implementing the Automated Career Advice Channel end-to-end.*

---
[← Index](README.md) · [← Prev](23-deployment-instructions.md) · [Next →](25-notifications-alerting.md)
