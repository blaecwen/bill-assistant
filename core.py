import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from llm import LLMError, call_llm
from state import PhotoStore, RateLimiter

logger = logging.getLogger(__name__)

_RATE_LIMIT_MSG = "Daily limit reached. Try again tomorrow."
_LLM_ERROR_MSG = "Sorry, something went wrong. Please try again."
_NO_PHOTO_MSG = "Please send a photo of the bill first."
_GOT_PHOTO_MSG = "Got your bill! What would you like to do? For example:\n• Split for 3 people\n• Alice had the pasta, Bob had the steak\n• What's the total?"
_STALE_REUSE_MSG = "Your bill photo is {age:.0f} min old. Reply 'yes' to reuse it, or send a new photo."
_STALE_REPROMPT_MSG = "Please send a new photo or reply 'yes' to reuse the old one."

_AFFIRMATIVE_WORDS = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
    "reuse", "use it", "use old", "keep", "same", "y",
})


def _is_affirmative(text: str) -> bool:
    normalized = text.strip().lower().rstrip("!.?,")
    return normalized in _AFFIRMATIVE_WORDS


@dataclass
class BillResponse:
    text: str
    needs_input: bool = False
    request_summary: str | None = None
    rate_limited: bool = False
    llm_error: bool = False


async def process_message(
    session_id: str,
    photo_store: PhotoStore,
    rate_limiter: RateLimiter,
    photo: Optional[bytes] = None,
    request: Optional[str | bytes] = None,
    request_type: Optional[Literal["text", "audio"]] = None,
    audio_format: Optional[str] = None,
    skip_stale_check: bool = False,
) -> BillResponse:
    # ------------------------------------------------------------------ #
    # CASE A: Photo provided (with or without request)                     #
    # ------------------------------------------------------------------ #
    if photo is not None:
        logger.debug("State transition: storing photo", extra={"session_id": session_id})

        # Recover any pending request before store_photo clears it
        pending = photo_store.get_pending_request(session_id)

        photo_store.store_photo(session_id, photo)

        # Determine the effective request: caption > recovered pending
        effective_request = request
        effective_request_type = request_type
        effective_audio_format = audio_format
        recovered_pending = False

        if effective_request is None and pending is not None:
            logger.debug(
                "Using pending request with new photo",
                extra={"session_id": session_id},
            )
            effective_request = pending
            effective_request_type = "text"
            recovered_pending = True

        if effective_request is not None:
            allowed = await rate_limiter.check_and_increment()
            if not allowed:
                return BillResponse(text=_RATE_LIMIT_MSG, rate_limited=True)

            if effective_request_type == "audio":
                user_hist = "[photo] [voice message]"
            elif recovered_pending:
                user_hist = f"[new photo] {effective_request}"
            else:
                user_hist = f"[photo] {effective_request}"

            return await _call_and_respond(
                session_id, photo, effective_request, effective_request_type,
                effective_audio_format, photo_store, user_hist,
            )

        photo_store.add_to_history(session_id, "user", "[photo]")
        photo_store.add_to_history(session_id, "assistant", _GOT_PHOTO_MSG)
        return BillResponse(text=_GOT_PHOTO_MSG, needs_input=True)

    # ------------------------------------------------------------------ #
    # CASE B: No photo in message, request provided                        #
    # ------------------------------------------------------------------ #
    if request is not None:
        # B1: Awaiting stale confirmation
        if photo_store.is_awaiting_stale_confirmation(session_id):
            if request_type == "text" and isinstance(request, str) and _is_affirmative(request):
                logger.debug(
                    "Stale confirmation: reusing old photo",
                    extra={"session_id": session_id},
                )
                photo_store.reset_photo_ttl(session_id)
                stored = photo_store.get_photo(session_id)
                pending = photo_store.get_pending_request(session_id)
                photo_store.clear_state(session_id)

                if stored is None:
                    return BillResponse(text=_NO_PHOTO_MSG, needs_input=True)

                if pending is None:
                    # No pending text request (e.g. prior request was audio, which
                    # can't be stored as pending). Photo is confirmed — ask what they want.
                    return BillResponse(text=_GOT_PHOTO_MSG, needs_input=True)

                allowed = await rate_limiter.check_and_increment()
                if not allowed:
                    return BillResponse(text=_RATE_LIMIT_MSG, rate_limited=True)

                return await _call_and_respond(
                    session_id, stored.data, pending, "text", None,
                    photo_store, pending,
                )
            else:
                # Arbitrary text while waiting — re-prompt
                return BillResponse(text=_STALE_REPROMPT_MSG, needs_input=True)

        # B2: No photo stored at all
        if photo_store.get_photo(session_id) is None:
            return BillResponse(text=_NO_PHOTO_MSG, needs_input=True)

        # B3: Photo is stale, not yet asked (skipped for web sessions)
        if not skip_stale_check and photo_store.is_photo_stale(session_id):
            text_request = request if isinstance(request, str) else None
            if text_request:
                photo_store.set_pending_request(session_id, text_request)
            photo_store.set_awaiting_stale_confirmation(session_id, True)

            stored = photo_store.get_photo(session_id)
            age_min = (
                (datetime.now(timezone.utc) - stored.stored_at).total_seconds() / 60
                if stored else 0
            )
            return BillResponse(
                text=_STALE_REUSE_MSG.format(age=age_min),
                needs_input=True,
            )

        # B4: Photo is fresh (or stale check skipped) — process
        allowed = await rate_limiter.check_and_increment()
        if not allowed:
            return BillResponse(text=_RATE_LIMIT_MSG, rate_limited=True)

        stored = photo_store.get_photo(session_id)
        user_hist = "[voice message]" if request_type == "audio" else (
            request if isinstance(request, str) else "[message]"
        )
        return await _call_and_respond(
            session_id, stored.data, request, request_type, audio_format,
            photo_store, user_hist,
        )

    # ------------------------------------------------------------------ #
    # CASE C: Nothing provided (defensive)                                 #
    # ------------------------------------------------------------------ #
    return BillResponse(text=_NO_PHOTO_MSG, needs_input=True)


async def _call_and_respond(
    session_id: str,
    photo_bytes: bytes,
    request: str | bytes,
    request_type: Optional[str],
    audio_format: Optional[str],
    photo_store: PhotoStore,
    user_history_text: str,
) -> BillResponse:
    request_text: Optional[str] = None
    audio_bytes: Optional[bytes] = None

    if request_type == "audio" and isinstance(request, bytes):
        audio_bytes = request
    elif isinstance(request, str):
        request_text = request

    history = photo_store.get_history(session_id)

    try:
        raw = await call_llm(
            photo_bytes=photo_bytes,
            request_text=request_text,
            audio_bytes=audio_bytes,
            audio_format=audio_format or "ogg",
            history=history,
        )
    except LLMError:
        logger.error("LLM error for session_id=%s", session_id)
        return BillResponse(text=_LLM_ERROR_MSG, llm_error=True)

    try:
        data = json.loads(raw)
        text = data["text"]
        request_summary = data.get("request_summary")
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("LLM returned malformed JSON for session_id=%s: %s", session_id, exc)
        return BillResponse(text=_LLM_ERROR_MSG, llm_error=True)

    photo_store.add_to_history(session_id, "user", user_history_text)
    photo_store.add_to_history(session_id, "assistant", text)
    return BillResponse(text=text, request_summary=request_summary)
