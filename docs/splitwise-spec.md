# Splitwise Integration Spec

## Overview

After a bill is split, users can push the result directly to Splitwise — no manual entry, no emails to type. The app connects via OAuth, fetches the user's friends, matches names from the split result automatically, and pushes the expense in one tap.

**Scope:** Web app only (Telegram excluded for now).

---

## UX Flow

### 1. Connect (once, ever)

A small "Connect Splitwise" button lives in the app — shown in the empty state or after a split result. Tapping it triggers the OAuth flow:

1. Backend redirects user to Splitwise OAuth authorize URL
2. User approves on Splitwise's page (standard OAuth consent screen)
3. Splitwise redirects back to the app
4. Backend exchanges the code for an access token, stores it server-side keyed by `session_id`
5. App returns to its previous state, now showing the user as connected

After connecting, the button disappears and a subtle "Splitwise connected" indicator takes its place.

### 2. After a Split Result

Every response card that contains a per-person breakdown shows a **"Save to Splitwise"** button at the bottom of the card.

Tapping it:
1. Backend fetches the user's Splitwise friends (cached after first fetch)
2. LLM matches bill names to friends (e.g. "Alice" → "Alice Johnson")
3. App shows a confirmation sheet:

```
Save to Splitwise

Alice Johnson     $18.50
Bob Smith         $21.00
You               $15.00  (you paid $54.50 total)

[Save]   [Cancel]
```

4. User taps **Save** → expense created in Splitwise → brief success state on the card

**Total taps after setup: 2** (Save to Splitwise → Save).

### 3. Edge Cases

| Situation | Behaviour |
|---|---|
| Name not matched | Excluded silently; noted below the list: _"Charlie wasn't found in your Splitwise contacts"_ |
| Multiple possible matches | Show disambiguating buttons: _"Alice: Alice Johnson or Alice Wang?"_ |
| Not connected | Tapping "Save to Splitwise" triggers the connect flow first, then returns to confirmation |
| Splitwise token expired | Re-trigger OAuth silently on next tap |

---

## Name Matching

Bill names (e.g. "Alice", "Bob") are matched against the user's Splitwise friends list using the LLM — the same model already in the stack. A small secondary call is made with:

- The list of names from the split result
- The user's Splitwise friends (first name, last name)

The LLM returns a mapping: `{bill_name → friend_id}`. This handles nicknames, partial names, and abbreviations more robustly than fuzzy string matching.

**Who paid:** Always the connected user (the person running the app). They are set as `paid_share = total`, `owed_share = their_portion`.

---

## Backend: New Endpoints

### `GET /splitwise/connect`
Redirects to Splitwise OAuth authorize URL.

```
Params (query):
  session_id: string

Redirects to:
  https://secure.splitwise.com/oauth/authorize
    ?client_id=...
    &redirect_uri=<backend>/splitwise/callback
    &response_type=code
    &state=<session_id>
```

### `GET /splitwise/callback`
Handles the OAuth redirect from Splitwise. Exchanges code for token, stores it, redirects back to the web app.

```
Params (query):
  code:  string
  state: string  (session_id passed through)

On success:
  Stores access token server-side keyed by session_id
  Redirects to: <web_app_url>?splitwise=connected
```

### `GET /splitwise/friends`
Returns the user's Splitwise friends. Cached in session state after first fetch.

```
Headers:
  (session_id passed as query param or header)

Response 200:
  {
    "friends": [
      { "id": 123, "first_name": "Alice", "last_name": "Johnson" },
      ...
    ]
  }

Errors:
  401 — not connected
  502 — Splitwise API failure
```

### `POST /splitwise/push`
Creates an expense in Splitwise.

```
Request (JSON):
  {
    "session_id": "...",
    "description": "Dinner split",   // from request_summary
    "total": 54.50,
    "splits": [
      { "splitwise_user_id": 123, "owed_share": 18.50 },
      { "splitwise_user_id": 456, "owed_share": 21.00 }
    ]
  }

Response 200:
  { "expense_id": 789 }

Errors:
  401 — not connected
  502 — Splitwise API failure
```

---

## Backend: Splitwise API Calls Used

| Call | Splitwise endpoint | When |
|---|---|---|
| Exchange code for token | `POST /oauth/token` | OAuth callback |
| Get current user | `GET /api/v3.0/get_current_user` | After connect (get own user ID) |
| Get friends | `GET /api/v3.0/get_friends` | On `/splitwise/friends` |
| Create expense | `POST /api/v3.0/create_expense` | On `/splitwise/push` |

The `create_expense` call uses numeric Splitwise user IDs for all participants — the frontend sends `splitwise_user_id` values resolved during name matching.

---

## State Changes

`ChatState` (in `state.py`) gains:

```python
splitwise_token: str | None = None        # OAuth access token
splitwise_user_id: int | None = None      # own Splitwise user ID
splitwise_friends: list[dict] | None = None  # cached friends list
```

Token stored server-side only, never sent to the frontend.

---

## `BillResponse` Changes

A new optional field:

```python
splits: list[dict] | None = None
# e.g. [{"name": "Alice", "amount": 18.50}, {"name": "Bob", "amount": 21.00}]
```

Populated by the LLM when a per-person breakdown is returned. The frontend uses this to drive the Splitwise confirmation sheet. Only present when the response contains a genuine split (not totals, questions, etc.).

The LLM JSON response format gains an optional field:
```json
{
  "text": "...",
  "request_summary": "...",
  "splits": [
    { "name": "Alice", "amount": 18.50 },
    { "name": "Bob", "amount": 21.00 }
  ]
}
```

The system prompt is updated to instruct the LLM to populate `splits` when it computes a per-person breakdown, and omit it otherwise.

---

## Env Vars

| Variable | Required | Notes |
|---|---|---|
| `SPLITWISE_CLIENT_ID` | Yes (if feature enabled) | From Splitwise developer portal |
| `SPLITWISE_CLIENT_SECRET` | Yes (if feature enabled) | Never sent to frontend |
| `SPLITWISE_REDIRECT_URI` | Yes | Must match what's registered on Splitwise |

Feature is disabled entirely if `SPLITWISE_CLIENT_ID` is not set.

---

## Out of Scope (for now)

- Telegram bot Splitwise integration
- Selecting who paid (always assumed to be the connected user)
- Group expenses (creates a non-group expense between individuals)
- Persistent token storage (token lost on server restart — user re-connects)

---

## Future

- Store tokens in a proper DB so reconnect isn't needed after deploys
- Let user select a Splitwise group to post into
- Support "split equally" shortcut without needing named splits
- Show past Splitwise pushes in bill history
