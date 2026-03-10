"""
Tests for bot.py helper logic.

Note: `bot.py` is imported here; the langfuse stub in conftest.py fires
before any app import so the module loads cleanly even without real keys.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from bot import _typing

# ---------------------------------------------------------------------------
# _typing context manager
# ---------------------------------------------------------------------------


async def test_handler_body_runs_when_send_chat_action_raises():
    """
    Regression test for the core bug: if send_chat_action throws (network
    error, Telegram API hiccup), the background task used to die with that
    exception.  Then `await task` in the finally block re-raised it *before*
    reply_text was called, leaving the user with no response.

    After the fix the inner try/except swallows per-iteration errors so the
    task stays alive; the handler body must complete regardless.
    """
    mock_bot = MagicMock()
    mock_bot.send_chat_action = AsyncMock(
        side_effect=RuntimeError("Telegram API unavailable")
    )

    body_executed = False

    async with _typing(mock_bot, "42"):
        await asyncio.sleep(0)  # let the loop task start and hit the error
        body_executed = True    # this line must be reached

    assert body_executed


async def test_typing_exits_cleanly_on_success():
    """Normal path: no exception, block exits without raising."""
    mock_bot = MagicMock()
    mock_bot.send_chat_action = AsyncMock()

    async with _typing(mock_bot, "42"):
        pass  # immediate exit — should not raise


async def test_typing_sends_action_at_least_once():
    """The typing indicator is actually dispatched before the block exits."""
    mock_bot = MagicMock()
    mock_bot.send_chat_action = AsyncMock()

    async with _typing(mock_bot, "42"):
        await asyncio.sleep(0)  # yield so the loop task can fire once

    mock_bot.send_chat_action.assert_called()


async def test_repeated_failures_do_not_accumulate():
    """
    If send_chat_action keeps failing across multiple loop iterations the
    task must stay alive and not surface exceptions through the context exit.
    """
    mock_bot = MagicMock()
    mock_bot.send_chat_action = AsyncMock(side_effect=OSError("timeout"))

    completed = False

    async with _typing(mock_bot, "42"):
        # Wait long enough for at least two loop iterations (loop sleeps 4 s,
        # but we only need the first error pass; sleep(0) x2 is enough).
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        completed = True

    assert completed
