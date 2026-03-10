import sys
from unittest.mock import MagicMock

# langfuse 3.x still uses pydantic v1 internally which crashes on Python 3.14.
# Since tests always mock call_llm, we never need real langfuse at test time.
# Stub the package before any application module imports it.
_langfuse_stub = MagicMock()
_langfuse_stub.observe = lambda **kwargs: (lambda f: f)  # no-op passthrough decorator
sys.modules["langfuse"] = _langfuse_stub
sys.modules["langfuse.openai"] = MagicMock()
sys.modules["langfuse.decorators"] = MagicMock()

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from state import PhotoStore, RateLimiter

CHAT = "chat_123"
PHOTO = b"fake-photo-bytes"
LLM_REPLY = "Total: $45.00. Split for 3: $15.00 each."


@pytest.fixture
def ps():
    return PhotoStore(ttl_minutes=30, retain_days=7)


@pytest.fixture
def rl():
    return RateLimiter(daily_limit=100)


@pytest.fixture
def mock_llm():
    with patch("core.call_llm", new_callable=AsyncMock) as m:
        m.return_value = LLM_REPLY
        yield m
