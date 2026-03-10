"""
Tests for core.process_message() state machine.

Coverage targets:
- All major branches (photo-only, caption shortcut, fresh follow-up, stale flows, errors)
- Points of failure: rate limiting, LLM errors, stale+pending recovery
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from core import (
    process_message,
    _RATE_LIMIT_MSG,
    _LLM_ERROR_MSG,
    _NO_PHOTO_MSG,
    _GOT_PHOTO_MSG,
)
from llm import LLMError
from state import PhotoStore, RateLimiter
from tests.conftest import CHAT, PHOTO, LLM_REPLY


def make_stale(ps: PhotoStore, chat_id: str = CHAT, minutes_ago: int = 60) -> None:
    """Backdate a stored photo to push it past the TTL."""
    ps._states[chat_id].photo.stored_at = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    )


async def msg(ps, rl, **kwargs):
    """Thin wrapper to avoid repeating CHAT in every call."""
    return await process_message(CHAT, ps, rl, **kwargs)


# ---------------------------------------------------------------------------
# Photo intake
# ---------------------------------------------------------------------------

async def test_photo_only_prompts_for_request(ps, rl, mock_llm):
    resp = await msg(ps, rl, photo=PHOTO)

    assert resp.needs_input is True
    mock_llm.assert_not_called()


async def test_caption_shortcut_calls_llm_immediately(ps, rl, mock_llm):
    """Photo + caption should process in one step, no follow-up needed."""
    resp = await msg(ps, rl, photo=PHOTO, request="split for 3", request_type="text")

    assert resp.text == LLM_REPLY
    assert resp.needs_input is False
    mock_llm.assert_called_once()


async def test_new_photo_replaces_old(ps, rl, mock_llm):
    new_photo = b"new-photo-bytes"
    await msg(ps, rl, photo=PHOTO)
    await msg(ps, rl, photo=new_photo)
    await msg(ps, rl, request="total?", request_type="text")

    _, kwargs = mock_llm.call_args
    assert kwargs["photo_bytes"] == new_photo


# ---------------------------------------------------------------------------
# Fresh photo follow-up flow
# ---------------------------------------------------------------------------

async def test_text_after_fresh_photo_calls_llm(ps, rl, mock_llm):
    await msg(ps, rl, photo=PHOTO)
    resp = await msg(ps, rl, request="split for 2", request_type="text")

    assert resp.text == LLM_REPLY
    assert resp.needs_input is False


async def test_voice_after_fresh_photo_passes_audio_bytes(ps, rl, mock_llm):
    voice = b"ogg-audio-data"
    await msg(ps, rl, photo=PHOTO)
    resp = await msg(ps, rl, request=voice, request_type="audio", audio_format="ogg")

    assert resp.text == LLM_REPLY
    mock_llm.assert_called_once()
    _, kw = mock_llm.call_args
    assert kw["photo_bytes"] == PHOTO
    assert kw["request_text"] is None
    assert kw["audio_bytes"] == voice
    assert kw["audio_format"] == "ogg"


async def test_text_with_no_photo_asks_for_photo(ps, rl, mock_llm):
    resp = await msg(ps, rl, request="split for 2", request_type="text")

    assert resp.text == _NO_PHOTO_MSG
    assert resp.needs_input is True
    mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# Stale photo flows
# ---------------------------------------------------------------------------

async def test_stale_photo_triggers_reuse_prompt(ps, rl, mock_llm):
    await msg(ps, rl, photo=PHOTO)
    make_stale(ps)
    resp = await msg(ps, rl, request="split for 3", request_type="text")

    assert resp.needs_input is True
    assert "min old" in resp.text
    mock_llm.assert_not_called()


async def test_stale_photo_stores_pending_request(ps, rl, mock_llm):
    """The user's request must survive until they confirm photo reuse."""
    await msg(ps, rl, photo=PHOTO)
    make_stale(ps)
    await msg(ps, rl, request="split for 4", request_type="text")

    assert ps.get_pending_request(CHAT) == "split for 4"


async def test_yes_after_stale_processes_with_pending_request(ps, rl, mock_llm):
    await msg(ps, rl, photo=PHOTO)
    make_stale(ps)
    await msg(ps, rl, request="split for 4", request_type="text")

    resp = await msg(ps, rl, request="yes", request_type="text")

    assert resp.text == LLM_REPLY
    assert resp.needs_input is False
    mock_llm.assert_called_once()
    _, kw = mock_llm.call_args
    assert kw["photo_bytes"] == PHOTO
    assert kw["request_text"] == "split for 4"
    assert kw["audio_bytes"] is None
    assert kw["audio_format"] == "ogg"


async def test_yes_after_stale_history_contains_prior_exchange(ps, rl, mock_llm):
    """History passed to LLM should include the initial photo intake."""
    await msg(ps, rl, photo=PHOTO)
    make_stale(ps)
    await msg(ps, rl, request="split for 4", request_type="text")
    await msg(ps, rl, request="yes", request_type="text")

    _, kw = mock_llm.call_args
    history = kw["history"]
    assert len(history) >= 2
    assert history[0].role == "user"
    assert "[photo]" in history[0].content
    assert history[1].role == "assistant"


async def test_non_affirmative_during_confirmation_reprompts_and_preserves_pending(ps, rl, mock_llm):
    await msg(ps, rl, photo=PHOTO)
    make_stale(ps)
    await msg(ps, rl, request="split for 4", request_type="text")

    resp = await msg(ps, rl, request="hmm what was the bill again", request_type="text")

    assert resp.needs_input is True
    assert ps.get_pending_request(CHAT) == "split for 4"  # not lost
    mock_llm.assert_not_called()


async def test_new_photo_while_awaiting_confirmation_processes_pending_immediately(ps, rl, mock_llm):
    """
    The trickiest flow: user sends text on stale photo, then sends a new photo
    instead of saying yes/no. The new photo should trigger immediate processing
    with the pending request — no extra prompt needed.
    """
    new_photo = b"new-photo-bytes"
    await msg(ps, rl, photo=PHOTO)
    make_stale(ps)
    await msg(ps, rl, request="split for 4", request_type="text")  # sets pending

    resp = await msg(ps, rl, photo=new_photo)  # new photo, no caption

    assert resp.text == LLM_REPLY
    assert resp.needs_input is False
    mock_llm.assert_called_once()
    _, kw = mock_llm.call_args
    assert kw["photo_bytes"] == new_photo  # new photo, not the stale one
    assert kw["request_text"] == "split for 4"
    assert kw["audio_bytes"] is None
    assert kw["audio_format"] == "ogg"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

async def test_rate_limit_blocks_fresh_follow_up(ps, mock_llm):
    rl = RateLimiter(daily_limit=0)
    await msg(ps, rl, photo=PHOTO)
    resp = await msg(ps, rl, request="split for 2", request_type="text")

    assert resp.text == _RATE_LIMIT_MSG
    mock_llm.assert_not_called()


async def test_rate_limit_blocks_caption_shortcut(ps, mock_llm):
    rl = RateLimiter(daily_limit=0)
    resp = await msg(ps, rl, photo=PHOTO, request="split for 3", request_type="text")

    assert resp.text == _RATE_LIMIT_MSG
    mock_llm.assert_not_called()


async def test_rate_limit_blocks_stale_confirmation(mock_llm):
    """Rate limit must fire even when the user says 'yes' to reuse a stale photo."""
    ps = PhotoStore(ttl_minutes=30, retain_days=7)
    rl = RateLimiter(daily_limit=0)

    await process_message(CHAT, ps, rl, photo=PHOTO)
    make_stale(ps)
    await process_message(CHAT, ps, rl, request="split for 2", request_type="text")
    resp = await process_message(CHAT, ps, rl, request="yes", request_type="text")

    assert resp.text == _RATE_LIMIT_MSG
    mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

async def test_llm_error_returns_friendly_message(ps, rl):
    with patch("core.call_llm", new_callable=AsyncMock) as m:
        m.side_effect = LLMError("upstream timeout")
        await msg(ps, rl, photo=PHOTO)
        resp = await msg(ps, rl, request="split for 2", request_type="text")

    assert resp.text == _LLM_ERROR_MSG
    assert resp.needs_input is False
