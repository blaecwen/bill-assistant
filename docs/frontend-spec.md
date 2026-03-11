# Split My Bill — Frontend Spec (Lovable)

## What It Is
Mobile-first web app. User photographs a bill, records a voice request (e.g. "split for 3"), AI responds with the breakdown.

**MVP:** Stateless (nothing persists across reloads), anonymous (no auth), voice-only input.

## Design
- Clean minimal, light theme.
- Mobile-first. Desktop: center the app in a phone-width container (~430px max) with background fillers. No separate desktop layout.
- No navbar, no sidebar, no tabs. One vertical scroll surface + action buttons.
- Bumble/Tinder profile scroll is the core UX metaphor — receipt image is the "first photo," responses are the "bio section" you scroll down to.

## Flow

### 1. Empty State
- Receipt-shaped placeholder in the center with a prompt to upload or capture.
- Two buttons: **Take Photo** (opens camera) and **Upload** (opens gallery).
- Minimalist, airy, inviting. That's it.

### 2. Receipt View (Photo Loaded)
- Receipt image fills ~85% of the viewport height. Fit-to-width, scroll vertically if taller.
- User must be able to read the receipt clearly while recording — this is the core experience.
- **Action bar** pinned at the bottom:
  - **Mic button** — large, centered, primary. The main CTA.
  - **New photo / Upload** — small secondary buttons flanking the mic.
- **New photo clears everything** — uploading a new photo resets the session: clears all response cards, scrolls back to top, starts fresh.

### 3. Recording
- **Tap to start**, **tap to stop**. No hold-to-record.
- While recording: mic button pulses/changes color, shows recording indicator (timer or waveform). Receipt stays visible.
- Secondary buttons hidden or dimmed during recording.
- After stopping: audio is sent to backend, transitions to Processing State (step 4).

### 4. Processing State
- As soon as recording stops, a new card placeholder appears below the receipt (or below existing response cards).
- The card shows the scroll-down hint if it's the first response, pulling the user to scroll.
- Inside the card: a spinner or shimmer skeleton with something like "Crunching your bill..." — enough to signal that a response is coming.
- Mic button and secondary buttons (new photo / upload) all disabled during processing — no new recordings or uploads until the response arrives or times out.
- **Timeout: 60 seconds.** If no response, show error card: "Request timed out. Tap to retry."
- Once the response arrives (or errors out), the placeholder is replaced with the full response card and buttons re-enable.

### 5. Response Cards (Bumble-style scroll)
- Responses appear below the receipt image, revealed by scrolling down — same pattern as scrolling past a Bumble profile photo to the bio.
- Subtle scroll hint (chevron, gradient, or card peek) so user knows there's content below.
- Each card shows:
  - **Summary of user's request** — from the backend `request_summary` field (LLM-generated, no client-side STT). Muted, small.
  - **AI response** — plain text, clean, readable.
- Multiple requests on the same bill **stack** chronologically (newest at bottom).
- Auto-scroll to new card when it arrives.
- Mic button stays visible (sticky bottom) throughout — but disabled during processing (see step 4).

## Image Handling
- Show the original image locally immediately for sharp readability.
- Compress client-side (target ~500KB) before sending to backend — user shouldn't wait for upload.
- Support common formats: jpg, png, heic, webp. Handle HEIC→JPEG conversion on client (iPhone users).

## Audio
- MediaRecorder API, browser-native format (WebM/Opus or MP4/AAC depending on browser).
- Send raw format + MIME type to backend — backend handles any conversion.

## Error Handling
- **Mic permission denied:** Show an inline message in the action bar area explaining mic access is needed, with a link/prompt to check browser settings. This is critical — voice is the only input method in MVP, so a blocked mic = dead end.
- **API failure / timeout:** The processing card turns into an error card: "Something went wrong. Tap to retry." Retries with the same audio. API calls time out at 60 seconds.
- **Daily limit reached:** Response card shows: "Daily limit reached. Try again tomorrow."

## Backend Integration

`POST /api/process` is implemented. Same `process_message` core as the Telegram bot — shared state, shared rate limiter.

See [API Integration Spec](api-integration-spec.md) for the full contract. Summary:

```
POST /api/process
Content-Type: multipart/form-data

Fields:
  - session_id: string (UUID v4, generated on page load, reset on new photo)
  - photo: file (compressed JPEG) — first request only
  - audio: file — format inferred from Content-Type, no separate field

Response 200:
  { "text": "...", "request_summary": "Split for 3 people" }

Errors:
  429 — daily limit reached
  500 — server error
```

- First request sends photo + audio. Follow-ups send just audio (backend remembers photo by `session_id`).
- New photo upload resets `session_id`, which also resets backend state for that session.

**For development:** Stub the API with a mock response after a 2-second delay so the full UI flow works without a running backend.

## Changelog

### 2026-03-11
- Backend Integration updated to match agreed API contract — see [API Integration Spec](api-integration-spec.md)
  - `chat_id` → `session_id`; reset on new photo upload
  - `audio_format` field dropped; format inferred from multipart Content-Type
  - Response: `transcript` → `request_summary`, `needs_input` removed
  - Daily limit returns `429`, not `200`

## Future Improvements
1. **Auto-parse on upload** — fire an LLM call as soon as the receipt is uploaded to parse and display structured items in the scroll-down section (before any voice request).
2. **Text input** — add a text field alongside the mic for typed requests.
3. **Quick action buttons** — preset buttons like "Split equally for 2/3/4" that skip recording.
4. **Hold-to-record** — support hold-to-record as an alternative to tap-to-toggle.
5. **Zoomable receipt** — pinch-to-zoom and pan on the receipt image.
6. **Bill history** — persist sessions, show recent bills on the empty state.
