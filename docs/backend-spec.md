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
) -> BillResponse:

@dataclass
class BillResponse:
    text: str                            # message to show the user
    needs_input: bool                    # True = waiting for more input (e.g. stale confirmation, no photo yet)
    request_summary: str | None = None  # LLM-generated summary of user's audio request
```

Calling patterns:
* Photo only (no caption): `process_message(session_id, ps, rl, photo=bytes)` → stores photo, returns prompt asking for request
* Photo + caption: `process_message(session_id, ps, rl, photo=bytes, request="split for 3", request_type="text")` → stores + processes
* Text/voice follow-up: `process_message(session_id, ps, rl, request="split for 3", request_type="text")` → uses stored photo
* Stale confirmation: user sends "yes" → same path, core handles internally

**State sharing across interfaces:** When the web API server is added, both it and the Telegram bot must share the same `PhotoStore` and `RateLimiter` instances (so the daily limit is global across both). The clean way to do this: the API server's lifespan creates the instances and starts the Telegram bot as a background task, passing the shared objects to both. `bot.py` will expose a factory `build_telegram_app(photo_store, rate_limiter)` rather than being the entry point. No global singletons needed.

## Stack
* Python 3.11+, python-telegram-bot v20+ (async, polling mode)
* FastAPI + uvicorn — web API server for the `POST /api/process` endpoint
* OpenRouter API via openai Python SDK (OpenRouter is OpenAI-compatible — switching models = changing one env var, no code changes)
* Langfuse for prompt management — system prompt fetched from Langfuse, cached locally for PROMPT_CACHE_TTL_MINUTES (default 10)
* Single multimodal LLM call: image + voice/text sent together in one request. Audio sent via input_audio content type. Configured model must support both vision and audio input. LLM returns structured JSON (`request_summary` + `text`); core parses it before returning `BillResponse`.

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
* INFO: every incoming message (type, session_id, timestamp), every LLM call (model, latency), photo stored/reused/expired/deleted
* WARNING: stale photo reuse, audio format issues
* ERROR: API failures, unexpected exceptions
* All processed photos should be logged (session_id, timestamp, file size) for auditing.

## Tracing
Use Langfuse tracing (@observe decorator) on all LLM calls. This gives cost tracking, latency, and full input/output history in the Langfuse dashboard — comes nearly free since the SDK is already integrated for prompt management.

## System Prompt
Managed in Langfuse (prompt name: bill-assistant). LLM must return a JSON response with two fields: `request_summary` (1-sentence clean summary of what the user asked, no filler words) and `text` (the answer). Initial version to seed:

```
You are a bill-splitting assistant. You receive a photo of a restaurant or store bill and a user request.
1. Read ALL items, quantities, and prices from the bill.
2. Identify subtotal, tax, service charge, discounts, and total.
3. Follow the user's request:
   - "Split for N" → divide total equally, show per-person amount
   - "Person A had X, Person B had Y" → assign items, split shared costs proportionally
   - "What's the total?" / "What did we order?" → read and list
Rules:
- Show your work: list items you read, then the calculation.
- Use the currency on the bill.
- Split tax/service proportionally unless told otherwise.
- If you can't read something, say so — don't guess.
- Use basic HTML formatting only: <b>bold</b> for emphasis, line breaks between items. No tables.
- If the request is ambiguous, ask a clarifying question.

Respond in JSON: {"request_summary": "...", "text": "..."}
```

Formatting uses basic HTML tags (`<b>`, line breaks) — compatible with Telegram HTML parse mode and web rendering. No markdown, no tables.

## Deployment
Runs on Coolify (Docker-based). Repo must include:
* Dockerfile — Python 3.11-slim base, install ffmpeg (for audio format conversion), copy code, pip install, CMD to start both bot and API server in the same process
* .env.example — template with all env vars listed above
* Coolify will inject env vars at runtime — no secrets in the repo

## Changelog

### 2026-03-11
- `chat_id` → `session_id` in core signature and all state management (pending implementation)
- `BillResponse` gains `request_summary: str | None = None` — LLM-generated, populated on audio requests
- LLM now returns structured JSON; core parses it before returning `BillResponse`
- System prompt: formatting updated to HTML tags (works for both Telegram and web); JSON response format added
- Added FastAPI + uvicorn + python-multipart for web API server
- Conversation history (10-message cap) already implemented — removed from Future
- Web API layer details: see [API Integration Spec](api-integration-spec.md)

## Future
* Persistent storage for photos/state (survive restarts, make 7-day retention real)
* Whisper fallback for vision-only models without audio support
* Multi-page bills — support multiple photos for a single bill
* Group chat support — multiple people claim items in real time
