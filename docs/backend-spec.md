# Telegram Bill Splitter Bot — Spec

## What It Does
Telegram bot that receives a photo of a bill + a user request (voice or text), sends both to a multimodal LLM via OpenRouter, and replies with the breakdown.

## Flow
1. User sends photo of a bill → bot stores it (per session_id), replies asking what to do.
2. User sends voice or text with their request → bot sends stored photo + request to LLM → replies with result.
Shortcut: If photo has a caption, treat it as the request and process immediately (skip step 2).
Follow-ups: Photo persists after processing. User can ask more questions about the same bill. Each follow-up includes the last 10 messages as context. Photo expires after PHOTO_TTL_MINUTES (default 30). New photo replaces old.
No photo stored? → Reply asking for one. Photo past TTL? → Keep it in memory but flag as stale. Ask if they want to reuse the old bill or upload a new one. If reuse, reset the TTL. Hard-delete photos after 7 days (background cleanup).
Stale photo + pending request flow: User sends text/voice → photo is stale → bot asks "reuse old bill or send a new one?" → if user sends a new photo, treat it as the photo for the request they already sent and process immediately. If user replies "yes" / affirmative text, reset TTL and process with the old photo. Note: if the original request was audio, it can't be stored as pending (bytes aren't serialisable as text). After stale confirmation, the user is prompted to re-record.

## Rate Limiting
Global daily limit: DAILY_REQUEST_LIMIT (default 100) API requests to OpenRouter across all users. Safeguard against unexpected spend. When limit is hit, reply: "Daily limit reached. Try again tomorrow." Track via simple in-memory counter that resets at midnight UTC.

## Core Module Contract
The core module exposes a single async function. Interface layers create `PhotoStore` and `RateLimiter` and pass them in:

```python
async def process_message(
    session_id: str,                     # unique session/user identifier
    photo_store: PhotoStore,             # caller-owned, passed explicitly
    rate_limiter: RateLimiter,           # caller-owned, passed explicitly
    photo: bytes | None = None,          # raw image bytes — if provided, stores/replaces
    request: str | bytes | None = None,  # text string OR audio bytes — if provided, triggers processing
    request_type: "text" | "audio" | None = None,
    audio_format: str | None = None,     # e.g. "ogg", "webm" — required if audio
    skip_stale_check: bool = False,      # web API passes True to bypass stale photo confirmation flow
) -> BillResponse:

@dataclass
class BillResponse:
    text: str                            # message to show the user
    needs_input: bool                    # True = waiting for more input (e.g. stale confirmation, no photo yet)
    request_summary: str | None = None  # LLM-generated summary of user's audio request
    rate_limited: bool = False           # True if daily request limit was hit
    llm_error: bool = False              # True if LLM call failed
```

Calling patterns:
* Photo only (no caption): `process_message(session_id, ps, rl, photo=bytes)` → stores photo, returns prompt asking for request
* Photo + caption: `process_message(session_id, ps, rl, photo=bytes, request="split for 3", request_type="text")` → stores + processes
* Text/voice follow-up: `process_message(session_id, ps, rl, request="split for 3", request_type="text")` → uses stored photo
* Stale confirmation: user sends "yes" → same path, core handles internally

**State sharing across interfaces:** When the web API server is added, shared state flows from a single orchestrator — neither `bot.py` nor `api.py` knows about each other:

```
main.py  ← creates PhotoStore + RateLimiter, runs everything
  ├── bot.py  → build_telegram_app(photo_store, rate_limiter)
  └── api.py  → build_fastapi_app(photo_store, rate_limiter)
```

`bot.py` becomes a factory module (no `if __name__ == "__main__"`). `api.py` is a pure FastAPI app. `main.py` is the single entry point and the only place state is created. Dockerfile CMD changes to `python main.py`.

## Stack
* Python 3.11+, python-telegram-bot v20+ (async, polling mode)
* FastAPI + uvicorn — web API server for the `POST /api/process` endpoint
* OpenRouter API via openai Python SDK (OpenRouter is OpenAI-compatible — switching models = changing one env var, no code changes)
* Langfuse for prompt management — system prompt fetched from Langfuse, cached locally for PROMPT_CACHE_TTL_MINUTES (default 10)
* Single multimodal LLM call: image + voice/text sent together in one request. Audio sent via input_audio content type. Configured model must support both vision and audio input. LLM returns structured JSON (`request_summary` + `text`) via `response_format=json_object`; core parses it before returning `BillResponse`.

## Target Models
All target models support vision, audio input, and `response_format=json_object`. Switching is one env var change.

| Provider | Model (OpenRouter slug) |
|---|---|
| Google | `google/gemini-2.5-flash` (default), `google/gemini-2.5-pro` |
| Anthropic | `anthropic/claude-opus-4-5`, `anthropic/claude-sonnet-4-5` |
| OpenAI | `openai/gpt-4o`, `openai/gpt-4o-mini` |
| xAI | `x-ai/grok-2-vision-1212` |

## Env Vars

| Variable | Required | Default |
|---|---|---|
| TELEGRAM_BOT_TOKEN | Yes | — |
| OPENROUTER_API_KEY | Yes | — |
| LANGFUSE_PUBLIC_KEY | Yes | — |
| LANGFUSE_SECRET_KEY | Yes | — |
| LANGFUSE_HOST | No | https://cloud.langfuse.com |
| LLM_MODEL | No | google/gemini-2.5-flash |
| PHOTO_TTL_MINUTES | No | 30 |
| PHOTO_RETAIN_DAYS | No | 7 |
| PROMPT_CACHE_TTL_MINUTES | No | 10 |
| LOG_LEVEL | No | INFO |
| DAILY_REQUEST_LIMIT | No | 100 |

## Commands
* /start, /help — usage instructions with examples

## Logging
* Use Python logging with configurable level via LOG_LEVEL env var (default INFO).
* DEBUG: raw LLM request/response payloads, state transitions
* INFO: every incoming message (type, session_id, preview), every LLM call (model, latency, request preview), photo stored/expired/deleted (session_id, size_kb)
* WARNING: stale photo reuse, rate limit reached, LLM response not valid JSON
* ERROR: API failures, unexpected exceptions
* `GET /health` access logs suppressed at INFO (too noisy); visible at DEBUG.

## Tracing
Use Langfuse tracing (@observe decorator) on all LLM calls. This gives cost tracking, latency, and full input/output history in the Langfuse dashboard — comes nearly free since the SDK is already integrated for prompt management.

## System Prompt
Managed in Langfuse (prompt name: `bill-assistant`). Seeded via `seed_prompt.py`. The prompt covers:

* **Scope gating** — only handle bill-reading and bill-splitting requests; deflect everything else with a single fixed sentence
* **Injection defense** — text in bill images and item names is bill data only, never instructions; standard jailbreak patterns are explicitly ignored
* **JSON output format** — always respond with `{"text": "...", "request_summary": "..."}`, no prose outside the object, no code fences; enforced at API level via `response_format=json_object`
* **Response style** — lead with bold main result, 2–4 supporting lines, stop there; full breakdown only on explicit request
* **Formatting** — Telegram HTML (`<b>bold</b>`, plain hyphens for lists); no markdown, no tables

## API Endpoints
* `POST /api/process` — main bill processing endpoint (multipart/form-data: session_id, photo?, audio)
* `GET /health` — health check, returns 200 with empty body; used by Coolify

## Deployment
Runs on Coolify (Docker-based). Repo must include:
* Dockerfile — Python 3.11-slim base, installs ffmpeg + curl, pip install, CMD `python main.py`
* .env.example — template with all env vars listed above
* Coolify will inject env vars at runtime — no secrets in the repo
* Coolify health check: `GET /health` on port 8000

## Changelog

### 2026-03-11 (session 2)
- `chat_id` → `session_id` in core signature and all state management
- `bot.py` refactored to factory module (`build_telegram_app(photo_store, rate_limiter)`); `main.py` added as single entry point that creates shared state and runs bot + API server concurrently
- `BillResponse` gains `request_summary: str | None = None` — LLM-generated, populated on audio requests
- `BillResponse` gains `rate_limited: bool` and `llm_error: bool` — API layer maps them to 429/500 without string comparisons
- `process_message` gains `skip_stale_check: bool = False` — web API passes `True` to bypass stale photo confirmation flow
- LLM now returns structured JSON; core parses it before returning `BillResponse`
- System prompt: formatting updated to HTML tags (works for both Telegram and web); JSON response format added
- `api.py` added: `POST /api/process` endpoint with CORS, multipart form handling, audio format inference, session validation
- Added FastAPI + uvicorn + python-multipart for web API server; httpx added for testing
- Conversation history (10-message cap) already implemented — removed from Future
- Langfuse fallback prompt removed — startup crash if Langfuse unavailable; SDK caches prompt after first successful fetch

### 2026-03-11 (session 2)
- Typing indicator (`ChatAction.TYPING`) while processing — background task, survives transient API errors
- Prompt updated: scope gating (bill topics only), injection defense, explicit JSON output format with example
- `response_format=json_object` added to LLM API call; `_parse_llm_response()` replaces raw `json.loads` with graceful fallback
- `GET /health` endpoint added for Coolify health checks; curl added to Dockerfile
- Logging overhaul: startup config banner, session_id/size_kb/latency surfaced in message strings (were silently dropped in `extra={}` with basicConfig), rate limit hits now log WARNING
- Health check access logs suppressed at INFO level (too noisy)
- Target models documented

## Future
* Persistent storage for photos/state (survive restarts, make 7-day retention real)
* Whisper fallback for vision-only models without audio support
* Multi-page bills — support multiple photos for a single bill
* Group chat support — multiple people claim items in real time
