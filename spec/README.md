# Automated Career Advice Channel — Technical Specification & Build Plan

> **Purpose of this document:** This is a complete, self-contained engineering specification for an autonomous content pipeline that produces high-quality YouTube career-advice scripts. It is written so that a coding agent (e.g., Claude Code) can implement the entire project end-to-end **without needing any additional context**. Every chapter is intentionally scoped: detailed enough to remove ambiguity, concise enough to stay actionable.

> **One-line summary:** A multi-agent system — **Data Fetcher → Script Generator → Judge → Voiceover → Visuals → Render → Publish** — orchestrated as a fully resumable pipeline, grounded in real labor-market data, gated by a quality rubric that enforces actionable, non-generic, factually-accurate advice, and carried all the way to a privacy-gated YouTube upload with built-in synthetic-content disclosure. A thin human layer only spot-checks and gives the final go-live OK.

---

## Table of Contents

**Core content pipeline (Agents 1–3)**
1. [Project Overview & Goals](01-project-overview-goals.md)
2. [System Architecture](02-system-architecture.md)
3. [Technology Stack & Dependencies](03-technology-stack-dependencies.md)
4. [Full File & Directory Structure](04-full-file-directory-structure.md)
5. [Database Schema](05-database-schema.md)
6. [Environment Variables & Configuration](06-environment-variables-configuration.md)
7. [Agent 1 — Data Fetcher](07-agent-1-data-fetcher.md)
8. [Agent 2 — Script Generator](08-agent-2-script-generator.md)
9. [Judge Agent](09-judge-agent.md)

**Production & publishing pipeline (Agents 4–7)**
10. [Agent 4 — Voiceover / TTS](10-agent-4-voiceover-tts.md)
11. [Agent 5 — Visuals & Thumbnail](11-agent-5-visuals-thumbnail.md)
12. [Agent 6 — Video Renderer](12-agent-6-video-renderer.md)
13. [Agent 7 — YouTube Publisher](13-agent-7-youtube-publisher.md)

**Orchestration, interfaces & operations**
14. [Pipeline Orchestrator](14-pipeline-orchestrator.md)
15. [Prompt Library](15-prompt-library.md)
16. [Template Definitions (All 6)](16-template-definitions-all-6.md)
17. [CLI Interface](17-cli-interface.md)
18. [Scheduler](18-scheduler.md)
19. [Output & Package Format Specification](19-output-package-format-specification.md)
20. [Human Review Dashboard](20-human-review-dashboard.md)
21. [Error Handling Strategy](21-error-handling-strategy.md)
22. [Testing Strategy](22-testing-strategy.md)
23. [Deployment Instructions](23-deployment-instructions.md)
24. [Sample Output Examples](24-sample-output-examples.md)
25. [Notifications & Alerting](25-notifications-alerting.md)

---
