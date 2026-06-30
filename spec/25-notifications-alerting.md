## 25. Notifications & Alerting

### 25.1 Purpose
Keep the operator informed without watching logs. Because the system runs largely unattended (and on a schedule), it **pushes** a short message to your phone for the few events that matter: a run finished, a draft needs your go-live approval, a video was uploaded, credits are running low, or a run failed.

### 25.2 Why Telegram (free, zero-infra)
- The **Telegram Bot API** is free, has no hosting requirement, and is a single HTTPS `POST` to `sendMessage` — no SDK, no webhook server needed for outbound alerts.
- Setup is one-time via **@BotFather** (get a `TELEGRAM_BOT_TOKEN`) plus your `TELEGRAM_CHAT_ID`.
- The design is pluggable, so Discord/Slack/email can be added later behind the same interface.

### 25.3 Notifier abstraction
```python
class Notifier(Protocol):
    def send(self, event: str, title: str, body: str, meta: dict | None = None) -> None: ...

class TelegramNotifier:   # POSTs to https://api.telegram.org/bot<token>/sendMessage
    ...
class NullNotifier:       # no-op (NOTIFY_ENABLED=false or NOTIFIER=none); used in tests
    ...
```
- Built from config by a small factory (`notifications/factory.py`); the orchestrator and agents depend only on the `Notifier` protocol.
- **Event filtering:** a notifier wrapper drops any event not listed in `NOTIFY_EVENTS`, so the operator controls verbosity from config.

### 25.4 Events
| Event | Fired by | When | Payload |
|-------|----------|------|---------|
| `run_complete` | Orchestrator | a run reaches its `to_stage` | `run_id`, verdict, final state, duration, YouTube URL (if any) |
| `need_validation` | Orchestrator / Publisher | a Private/Unlisted draft awaits go-live, or disclosure is `pending_manual_disclosure` | `run_id`, video URL, what to check |
| `video_uploaded` | Publisher (Agent 7) | upload succeeds | `run_id`, video URL, `privacy_status` |
| `low_credits` | Credit monitor / Provider layer | projected spend ≥ threshold, or a provider returns insufficient-quota/credit | provider, % of budget used, last error |
| `run_failed` | Orchestrator | run transitions to `FAILED` | `run_id`, failing `stage`, exception type/message |

Messages are concise, emoji-tagged for scannability, and include the `run_id` and a deep link to the dashboard run page where relevant.

### 25.5 Credit / budget monitoring
- A lightweight `CreditMonitor` accumulates **estimated** LLM/media spend per run (tokens × model price from a small price table) into a rolling monthly total persisted in the DB.
- When the projected monthly total crosses `LOW_CREDIT_THRESHOLD_PCT` of `MONTHLY_BUDGET_USD`, it emits **one** `low_credits` alert per period (debounced, not per call).
- Independently, any provider error that signals exhausted credit/quota (e.g., HTTP 429 `insufficient_quota`) triggers an immediate `low_credits` alert with the provider name.

### 25.6 One-time setup
1. In Telegram, message **@BotFather** → `/newbot` → copy the bot **token** into `TELEGRAM_BOT_TOKEN`.
2. Message your new bot once, then call `getUpdates` (or run `content-foundry notify-test`) to read your `chat.id` → put it in `TELEGRAM_CHAT_ID`.
3. Set `NOTIFY_ENABLED=true` and the desired `NOTIFY_EVENTS`. Verify with `content-foundry notify-test` (sends a sample of each event).

### 25.7 Reliability (never breaks the pipeline)
- Notifications are **best-effort and non-blocking**: `send()` is wrapped in try/except with a short `tenacity` retry; a delivery failure is logged but **never** fails a run.
- Sent on a background thread / fire-and-forget so alerting latency cannot slow the pipeline.
- In tests, `NullNotifier` records calls so assertions can verify the right events fire without any network.

### 25.8 Extensibility
Add a `DiscordNotifier`, `SlackNotifier`, or `EmailNotifier` implementing the same `Notifier.send()`; select via `NOTIFIER`. No pipeline code changes — only config.

---
[← Index](README.md) · [← Prev](24-sample-output-examples.md)
