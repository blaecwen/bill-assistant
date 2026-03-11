"""
Integration tests for POST /api/process.

Uses a real PhotoStore/RateLimiter injected into the FastAPI app,
with call_llm mocked at the core layer.
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from api import build_fastapi_app
from llm import LLMError
from state import PhotoStore, RateLimiter
from tests.conftest import PHOTO, LLM_REPLY, LLM_REPLY_TEXT

SESSION = "test-session-uuid"
AUDIO = b"fake-audio-bytes"


def _client(ps: PhotoStore, rl: RateLimiter) -> httpx.AsyncClient:
    app = build_fastapi_app(ps, rl)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


# ---------------------------------------------------------------------------
# Scenario 1: Photo + audio → 200
# ---------------------------------------------------------------------------

async def test_photo_and_audio_returns_200(ps, mock_llm):
    rl = RateLimiter(daily_limit=100)
    async with _client(ps, rl) as c:
        resp = await c.post(
            "/api/process",
            data={"session_id": SESSION},
            files={
                "photo": ("photo.jpg", PHOTO, "image/jpeg"),
                "audio": ("audio.webm", AUDIO, "audio/webm"),
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == LLM_REPLY_TEXT
    assert body["request_summary"] is not None
    mock_llm.assert_called_once()


# ---------------------------------------------------------------------------
# Scenario 2: Audio follow-up uses stored photo
# ---------------------------------------------------------------------------

async def test_audio_followup_uses_stored_photo(ps, mock_llm):
    rl = RateLimiter(daily_limit=100)
    async with _client(ps, rl) as c:
        # First request stores the photo and processes
        await c.post(
            "/api/process",
            data={"session_id": SESSION},
            files={
                "photo": ("photo.jpg", PHOTO, "image/jpeg"),
                "audio": ("audio.webm", AUDIO, "audio/webm"),
            },
        )
        # Follow-up: audio only — backend uses stored photo
        resp = await c.post(
            "/api/process",
            data={"session_id": SESSION},
            files={"audio": ("audio.webm", AUDIO, "audio/webm")},
        )
    assert resp.status_code == 200
    assert resp.json()["text"] == LLM_REPLY_TEXT
    assert mock_llm.call_count == 2
    _, kw = mock_llm.call_args
    assert kw["photo_bytes"] == PHOTO


# ---------------------------------------------------------------------------
# Scenario 3: Missing session_id → 400
# ---------------------------------------------------------------------------

async def test_missing_session_id_returns_400(ps):
    rl = RateLimiter(daily_limit=100)
    async with _client(ps, rl) as c:
        resp = await c.post(
            "/api/process",
            files={"audio": ("audio.webm", AUDIO, "audio/webm")},
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"


# ---------------------------------------------------------------------------
# Scenario 3b: Missing audio → 400
# ---------------------------------------------------------------------------

async def test_missing_audio_returns_400(ps):
    rl = RateLimiter(daily_limit=100)
    async with _client(ps, rl) as c:
        resp = await c.post(
            "/api/process",
            data={"session_id": SESSION},
            files={"photo": ("photo.jpg", PHOTO, "image/jpeg")},
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"


# ---------------------------------------------------------------------------
# Scenario 4: Audio with no photo stored → 200, response asks for photo
# ---------------------------------------------------------------------------

async def test_audio_with_no_photo_asks_for_photo(ps, mock_llm):
    rl = RateLimiter(daily_limit=100)
    async with _client(ps, rl) as c:
        resp = await c.post(
            "/api/process",
            data={"session_id": SESSION},
            files={"audio": ("audio.webm", AUDIO, "audio/webm")},
        )
    assert resp.status_code == 200
    assert resp.json()["text"]  # non-empty — tells user to send a photo
    mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 5: Daily limit reached → 429
# ---------------------------------------------------------------------------

async def test_daily_limit_returns_429(ps, mock_llm):
    rl = RateLimiter(daily_limit=0)
    async with _client(ps, rl) as c:
        resp = await c.post(
            "/api/process",
            data={"session_id": SESSION},
            files={
                "photo": ("photo.jpg", PHOTO, "image/jpeg"),
                "audio": ("audio.webm", AUDIO, "audio/webm"),
            },
        )
    assert resp.status_code == 429
    assert resp.json() == {"error": "daily_limit_reached"}
    mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 6: LLM failure → 500
# ---------------------------------------------------------------------------

async def test_llm_failure_returns_500(ps):
    rl = RateLimiter(daily_limit=100)
    with patch("core.call_llm", new_callable=AsyncMock) as m:
        m.side_effect = LLMError("timeout")
        async with _client(ps, rl) as c:
            resp = await c.post(
                "/api/process",
                data={"session_id": SESSION},
                files={
                    "photo": ("photo.jpg", PHOTO, "image/jpeg"),
                    "audio": ("audio.webm", AUDIO, "audio/webm"),
                },
            )
    assert resp.status_code == 500
    assert resp.json() == {"error": "server_error"}


# ---------------------------------------------------------------------------
# Scenario 7: audio/mp4 Content-Type → format inferred as "mp4"
# ---------------------------------------------------------------------------

async def test_mp4_audio_format_inferred(ps, mock_llm):
    rl = RateLimiter(daily_limit=100)
    async with _client(ps, rl) as c:
        await c.post(
            "/api/process",
            data={"session_id": SESSION},
            files={
                "photo": ("photo.jpg", PHOTO, "image/jpeg"),
                "audio": ("audio.mp4", AUDIO, "audio/mp4"),
            },
        )
    _, kw = mock_llm.call_args
    assert kw["audio_format"] == "mp4"
