# Splitwise Integration Spec

Web app only. Telegram excluded.

---

## UX

**Connect (once):** "Connect Splitwise" button in app → OAuth redirect → user approves → back to app, connected. Token stored server-side.

**After a split result:** Every response card with a per-person breakdown shows **"Save to Splitwise"**. Tap → backend matches names to friends via LLM → confirmation sheet:

```
Alice Johnson     $18.50
Bob Smith         $21.00
You               $15.00  (you paid $54.50)

[Save]   [Cancel]
```

Tap Save → pushed. **2 taps total after setup.**

**Edge cases:**

| Situation | Behaviour |
|---|---|
| Name unmatched | Excluded; noted: _"Charlie wasn't found in your Splitwise contacts"_ |
| Ambiguous match | Inline buttons: _"Alice: Alice Johnson or Alice Wang?"_ |
| Not connected | Connect flow triggers first, then returns to confirmation |
| Token expired | Re-trigger OAuth on next tap |

---

## Name Matching

LLM call with split names + friends list → returns `{bill_name → splitwise_user_id}`. Handles nicknames and partial names. Connected user always assumed payer (`paid_share = total`).

---

## New Backend Endpoints

**`GET /splitwise/connect?session_id=`**
Redirects to `https://secure.splitwise.com/oauth/authorize` with `client_id`, `redirect_uri`, `response_type=code`, `state=session_id`.

**`GET /splitwise/callback?code=&state=`**
Exchanges code for token, stores server-side by `session_id`, redirects to `<web_app_url>?splitwise=connected`.

**`GET /splitwise/friends?session_id=`**
Returns cached friends list.
```json
{ "friends": [{ "id": 123, "first_name": "Alice", "last_name": "Johnson" }] }
```
Errors: `401` not connected, `502` Splitwise failure.

**`POST /splitwise/push`**
```json
{
  "session_id": "...",
  "description": "Dinner split",
  "total": 54.50,
  "splits": [{ "splitwise_user_id": 123, "owed_share": 18.50 }]
}
```
Response: `{ "expense_id": 789 }`. Errors: `401`, `502`.

---

## Splitwise API Calls

| Endpoint | When |
|---|---|
| `POST /oauth/token` | Callback — exchange code for token |
| `GET /api/v3.0/get_current_user` | After connect — store own user ID |
| `GET /api/v3.0/get_friends` | `/splitwise/friends` |
| `POST /api/v3.0/create_expense` | `/splitwise/push` |

---

## Code Changes

**`state.py` — `ChatState` gains:**
```python
splitwise_token: str | None = None
splitwise_user_id: int | None = None
splitwise_friends: list[dict] | None = None
```

**`core.py` — `BillResponse` gains:**
```python
splits: list[dict] | None = None
# [{"name": "Alice", "amount": 18.50}, ...]
```
Only populated on per-person breakdowns, never on totals/questions.

**LLM JSON response gains optional field:**
```json
{ "text": "...", "request_summary": "...", "splits": [{"name": "Alice", "amount": 18.50}] }
```
System prompt updated to populate `splits` on breakdowns, omit otherwise.

---

## Env Vars

| Variable | Notes |
|---|---|
| `SPLITWISE_CLIENT_ID` | From Splitwise developer portal |
| `SPLITWISE_CLIENT_SECRET` | Never sent to frontend |
| `SPLITWISE_REDIRECT_URI` | Must match registered URI |

Feature disabled if `SPLITWISE_CLIENT_ID` unset.

---

## Out of Scope

- Who paid selection (always connected user)
- Group expenses
- Persistent token storage (lost on restart)

## Future
- DB-backed token storage
- Splitwise group selection
- Bill history with past pushes
