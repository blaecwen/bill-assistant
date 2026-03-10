# Bill Assistant

Telegram bot that splits bills. Send a photo of a receipt, ask a question in text or voice, get the breakdown.

## Setup

```bash
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

pip install -r requirements.txt

# Create the system prompt in Langfuse (run once)
python seed_prompt.py

# Start the bot
python bot.py
```

## Usage

1. Send a photo of a bill
2. Send a request — text or voice:
   - "Split for 3 people"
   - "Alice had the pasta, Bob had the steak"
   - "What's the total?"
3. Or: send the photo with a caption to do both in one step

The photo stays active for 30 minutes. Follow-up questions reuse the same bill.

## Files

| File | Purpose |
|---|---|
| `config.py` | Env var loading |
| `state.py` | In-memory photo store, TTL, rate limiter |
| `prompts.py` | Langfuse prompt management |
| `llm.py` | Multimodal OpenRouter call + Langfuse tracing |
| `core.py` | `process_message()` — all business logic |
| `bot.py` | Telegram handlers, entry point |
| `seed_prompt.py` | One-off script to create Langfuse prompt |

## Key env vars

| Var | Default | Notes |
|---|---|---|
| `LLM_MODEL` | `google/gemini-2.5-flash` | Must support vision + audio |
| `PHOTO_TTL_MINUTES` | `30` | After this, bot asks to reuse or upload new |
| `DAILY_REQUEST_LIMIT` | `100` | Global cap across all users |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for full LLM payloads |

## Deploy (Coolify)

Point Coolify at this repo. It builds the `Dockerfile` and injects env vars at runtime. No secrets in the repo.

## Extending

`core.process_message()` is interface-agnostic — it takes raw bytes and strings, returns a `BillResponse`. Wire any new interface (web, mobile) by calling it directly. See `SPEC.md` for the full contract.
