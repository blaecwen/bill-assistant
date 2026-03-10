"""
Tests for PhotoStore TTL logic and RateLimiter.

These are the two stateful components whose edge cases
are easy to get wrong (off-by-one on TTL boundaries,
day rollover logic for rate limiting).
"""
from datetime import datetime, timezone, timedelta

import pytest

from state import PhotoStore, RateLimiter

CHAT = "chat_abc"
PHOTO = b"some-photo-bytes"


# ---------------------------------------------------------------------------
# PhotoStore — TTL boundaries
# ---------------------------------------------------------------------------

def test_freshly_stored_photo_is_fresh():
    ps = PhotoStore(ttl_minutes=30, retain_days=7)
    ps.store_photo(CHAT, PHOTO)

    assert ps.is_photo_fresh(CHAT) is True
    assert ps.is_photo_stale(CHAT) is False


def test_photo_past_ttl_is_stale_not_fresh():
    ps = PhotoStore(ttl_minutes=30, retain_days=7)
    ps.store_photo(CHAT, PHOTO)
    ps._states[CHAT].photo.stored_at = datetime.now(timezone.utc) - timedelta(minutes=60)

    assert ps.is_photo_fresh(CHAT) is False
    assert ps.is_photo_stale(CHAT) is True


def test_photo_past_retain_window_is_neither():
    """
    Beyond 7 days it should be hard-deleted by the cleanup job.
    Until then it shouldn't appear as stale (which would offer a reuse prompt).
    """
    ps = PhotoStore(ttl_minutes=30, retain_days=7)
    ps.store_photo(CHAT, PHOTO)
    ps._states[CHAT].photo.stored_at = datetime.now(timezone.utc) - timedelta(days=8)

    assert ps.is_photo_fresh(CHAT) is False
    assert ps.is_photo_stale(CHAT) is False


def test_reset_ttl_makes_stale_photo_fresh():
    ps = PhotoStore(ttl_minutes=30, retain_days=7)
    ps.store_photo(CHAT, PHOTO)
    ps._states[CHAT].photo.stored_at = datetime.now(timezone.utc) - timedelta(minutes=60)
    assert ps.is_photo_stale(CHAT) is True

    ps.reset_photo_ttl(CHAT)

    assert ps.is_photo_fresh(CHAT) is True


def test_no_photo_is_neither_fresh_nor_stale():
    ps = PhotoStore(ttl_minutes=30, retain_days=7)

    assert ps.is_photo_fresh(CHAT) is False
    assert ps.is_photo_stale(CHAT) is False


# ---------------------------------------------------------------------------
# PhotoStore — cleanup
# ---------------------------------------------------------------------------

def test_cleanup_removes_only_expired_photos():
    ps = PhotoStore(ttl_minutes=30, retain_days=7)
    ps.store_photo("old", PHOTO)
    ps.store_photo("new", PHOTO)
    ps._states["old"].photo.stored_at = datetime.now(timezone.utc) - timedelta(days=8)

    count = ps.cleanup_expired()

    assert count == 1
    assert ps.get_photo("old") is None
    assert ps.get_photo("new") is not None


def test_cleanup_returns_zero_when_nothing_expired():
    ps = PhotoStore(ttl_minutes=30, retain_days=7)
    ps.store_photo(CHAT, PHOTO)

    assert ps.cleanup_expired() == 0


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

async def test_blocks_at_exactly_the_limit():
    rl = RateLimiter(daily_limit=2)
    await rl.check_and_increment()
    await rl.check_and_increment()

    assert await rl.check_and_increment() is False


async def test_counter_resets_when_date_advances():
    rl = RateLimiter(daily_limit=1)
    await rl.check_and_increment()
    assert await rl.check_and_increment() is False

    # Simulate midnight rollover
    rl._reset_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    assert await rl.check_and_increment() is True
