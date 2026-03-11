# API Integration Spec

Web API contract for `POST /api/process`. Will be merged into backend/frontend specs post-implementation.

---

## Naming

`chat_id` → `session_id` throughout core and all layers.

- **Web:** UUID v4, generated on page load, reset on new photo upload
- **Telegram:** `str(telegram_chat_id)`

---

## Request

```
POST /api/process
Content-Type: multipart/form-data
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | string | Yes | |
| `photo` | file (JPEG) | Conditional | First request only; omit on follow-ups |
| `audio` | file | Yes (MVP) | Format inferred from multipart `Content-Type` — no separate format field |

## Stack additions

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | `0.135.*` | Web API framework |
| `uvicorn[standard]` | `0.41.*` | ASGI server |
| `python-multipart` | `0.0.22` | Multipart form / file upload parsing (FastAPI dependency) |

---

## Audio

| Source | Format | Handling |
|---|---|---|
| Chrome / Firefox / Android | `audio/webm` (Opus) | Pass through |
| Safari / iOS | `audio/mp4` (AAC) | Pass through |
| Other | varies | Convert to WAV via ffmpeg |

The API server must always pass `audio_format` explicitly to `process_message` — never rely on the core's `"ogg"` default, which exists only for the Telegram bot.

---

## Response

### 200 OK

```json
{
  "text": "Here's the breakdown:\n- Person A: $18.50\n- Person B: $21.00",
  "request_summary": "Split between me and Alex"
}
```

| Field | Type | Notes |
|---|---|---|
| `text` | string | AI answer. Plain text, no markdown tables. |
| `request_summary` | string \| null | 1-sentence LLM-generated summary of the request, cleaned of filler. `null` only if no audio (future text input). |

Both fields come from a single structured LLM call — no separate STT step.

### Errors

Frontend owns all user-facing copy; HTTP status is the signal.

| Status | Scenario | Body |
|---|---|---|
| `429` | Daily limit reached | `{"error": "daily_limit_reached"}` |
| `400` | Bad request | `{"error": "bad_request", "detail": "..."}` |
| `500` | LLM / server failure | `{"error": "server_error"}` |

---

## Session expiry

Web: no stale photo flow. TTL is a Telegram safeguard only. API server bypasses stale checks for web sessions.

---

## LLM structured output

LLM returns JSON; core parses it before populating `BillResponse`.

```json
{
  "request_summary": "Split for 3 people",
  "text": "Here's the breakdown:\n..."
}
```

**Prompt:** one shared Langfuse prompt. `"No markdown tables (this is Telegram)"` → `"No markdown tables. Use plain text with line breaks."` Telegram-safe formatting works for web.

---

## `BillResponse` changes

```python
@dataclass
class BillResponse:
    text: str
    needs_input: bool                    # internal, Telegram layer only — not in HTTP API
    request_summary: str | None = None  # populated when audio was processed
```

---

## Test scenarios

Key integration tests for `POST /api/process`. All use a real (or stubbed) `PhotoStore`/`RateLimiter` passed into a `TestClient`.

| # | Scenario | Input | Expected |
|---|---|---|---|
| 1 | Photo + audio → 200 | `session_id`, `photo`, `audio` (webm) | `200`, `text` non-empty, `request_summary` non-null |
| 2 | Audio follow-up (no photo in request) | `session_id` only, `audio` | `200`, uses stored photo from scenario 1 |
| 3 | Missing `session_id` → 400 | no `session_id` | `400`, `{"error": "bad_request"}` |
| 4 | Audio with no photo stored → ask for photo | `session_id` (fresh), `audio` | `200`, response asks user to send a photo |
| 5 | Daily limit reached → 429 | `RateLimiter(daily_limit=0)`, valid request | `429`, `{"error": "daily_limit_reached"}` |
| 6 | LLM failure → 500 | LLM stubbed to raise `LLMError` | `500`, `{"error": "server_error"}` |
| 7 | `audio/mp4` Content-Type passes through | `audio` file with `Content-Type: audio/mp4` | format inferred as `mp4`, passed to core correctly |
