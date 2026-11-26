## 13. Agent 7 — YouTube Publisher

### 13.1 Purpose
Upload the finished video to YouTube as a **privacy-gated draft**, attach all metadata and the thumbnail, and **guarantee the synthetic-content disclosure** is handled before anything can go public. This is the compliance backbone of the system.

> **Post-publish comment & embedded `Sources:` provenance (current behavior).**
> - **Comment ordering:** every upload starts `private` first, then `set_privacy` transitions it to `unlisted` (or `public` when disclosed). The top comment (`RECOMMEND_COMMENT_ENABLED` watch-next list with live YouTube titles + `PUBLISH_TOP_COMMENT` affiliate/CTA block) is posted **strictly AFTER `set_privacy`**, because YouTube rejects comments on private videos; if a video intentionally stays `private`, the doomed call is skipped cleanly (`comment_skipped_private_video`).
> - **Bottom-of-description `Sources:` block (`production/seo.py`):** `collect_embedded_sources` + `format_sources_block` collect every verified external URL embedded visually in the video (`programmatic_graphic` `SOURCE //` pills + burned `citations.srt`, excluding `LOW_TIER_SOURCE_DOMAINS`), deduplicate in chronological scene order, and place `Sources:` as the **very last block** of the description (below hashtags). Angle brackets `<`/`>` are sanitized via `youtube_safe_text` so chapter titles never trigger HTTP 400 `invalidDescription`. Per-video `Script.suppress_creator_credential` and `Script.disclaimer` fields govern sensitive employer/topic uploads.

> **Back-filling metadata (`scripts/recategorize.py`).** `videos.update` **replaces** the whole
> snippet rather than patching it, so a naive category change wipes the description, tags and
> localisations. `youtube.recategorized_snippet()` therefore does a read-modify-write: it re-sends
> every writable snippet field (`title` and `categoryId` are both mandatory) and returns `None` when
> the video is already correct, so re-running is a no-op. The script is dry-run by default and needs
> `--apply` to write. Used once to move 16 existing uploads from 28 (Science & Technology) to 27
> (Education), which is where this channel's audience actually browses.

### 13.2 Inputs / outputs
- **Input:** `VideoAsset` + `Script` (title/description/tags) + `VisualPackage` (thumbnail).
- **Output:** `PublishResult` artifact → `output/runs/<run_id>/publish_result.json`, and a row in `publish_results`.

### 13.3 Processing flow
```mermaid
flowchart TD
    A[Load OAuth creds (refresh if needed)] --> B[Pick title (first title_option or operator choice)]
    B --> C[videos.insert: snippet + status=YOUTUBE_PRIVACY_STATUS]
    C --> D[thumbnails.set: upload thumbnail.png]
    D --> E[Set synthetic-content disclosure via API if supported]
    E --> F{Disclosure programmatically confirmed?}
    F -->|yes| G[disclosure_set=1]
    F -->|no| H[upload_status=pending_manual_disclosure\nforce privacy=private]
    G --> I{PUBLISH_MODE}
    H --> I
    I -->|draft| J[Stop: leave as Private/Unlisted draft]
    I -->|auto| K{public allowed?\n(disclosure_set & gate ok)}
    K -->|yes| L[Set status=public]
    K -->|no| J
    J --> M[Persist PublishResult + emit disclosure checklist]
    L --> M
```

### 13.4 Disclosure handling (non-negotiable)
Because programmatic control of YouTube's **"Altered or synthetic content"** toggle is not reliably exposed by the Data API:
1. The publisher **attempts** to set the disclosure field via the API.
2. If it cannot confirm it was set, the upload is **forced to Private**, `upload_status=pending_manual_disclosure`, and `package.md` includes a **mandatory checklist** instructing the operator to flip the toggle in YouTube Studio before publishing.
3. **A video can never reach `public` while `disclosure_set=0` and `REQUIRE_MANUAL_DISCLOSURE_BEFORE_PUBLIC=true`.** This is enforced in code, not just documented.

### 13.5 `PublishResult` schema (Pydantic)
```python
class PublishResult(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    stage: Literal["publish"] = "publish"
    youtube_video_id: str | None
    video_url: str | None
    privacy_status: str            # private|unlisted|public
    disclosure_set: bool
    upload_status: str             # uploaded|failed|pending_manual_disclosure
    chosen_title: str
    published_at: datetime | None
    provenance: Provenance
```

### 13.6 Auth & publisher abstraction
- **OAuth 2.0 (installed-app flow):** `client_secrets.json` → first run opens a consent screen; the refresh token is cached at `YOUTUBE_TOKEN_FILE`. Scope: `https://www.googleapis.com/auth/youtube.upload` (+ `youtube` for thumbnail).
- **`Publisher` protocol** → `YouTubePublisher`; a `DryRunPublisher` (no network) is used in tests and via `--dry-run`.
- Resumable, chunked uploads (`MediaFileUpload`, resumable=True) tolerate flaky connections.

### 13.7 Resumability hooks
- If a prior upload partially succeeded, `publish_result.json` records the `youtube_video_id`; re-running updates metadata/privacy instead of re-uploading.
- `--dry-run` produces a `PublishResult` with `upload_status="uploaded"` but no real upload — useful for end-to-end testing.

### 13.8 Failure modes
| Failure | Handling |
|---------|----------|
| Token expired/invalid | Refresh; if refresh fails, prompt re-consent |
| Quota exceeded (403) | Stop, persist `pending`, surface clear quota message |
| Upload interrupted | Resume chunked upload; retry with backoff |
| Disclosure unconfirmed | Force Private + `pending_manual_disclosure` + checklist |

---

---
[← Index](README.md) · [← Prev](12-agent-6-video-renderer.md) · [Next →](14-pipeline-orchestrator.md)
