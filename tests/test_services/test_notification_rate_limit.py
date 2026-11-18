"""Tests for notification_rate_limit — the Redis-backed throttle used
by the WhatsApp photo/document debounce and the overdue-reminder cap.

Only the fail-open path is tested directly without a live Redis — the
module silently falls through to "don't throttle" when Redis isn't
configured, which is the safest default. The actual TTL dedup is also
asserted using a fake Redis client so the test doesn't need infra.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import notification_rate_limit


@pytest.mark.asyncio
async def test_fail_open_when_no_redis():
    """No redis_url → the throttle never trips. Parents never lose a
    notification because the cache layer is unavailable."""
    # Reset probe state so the test runs the no-redis branch
    notification_rate_limit._redis_client = None
    notification_rate_limit._redis_probed = False

    with patch.object(
        notification_rate_limit, "get_settings",
        return_value=SimpleNamespace(redis_url=None),
    ):
        skip = await notification_rate_limit.should_skip(
            "photo_shared", uuid.uuid4(), ttl_seconds=1800,
        )
    assert skip is False


@pytest.mark.asyncio
async def test_throttle_trips_second_call_within_window():
    """A fake Redis stub: second call within TTL returns truthy "key
    exists" → helper reports skip. First call sets the key."""
    notification_rate_limit._redis_client = None
    notification_rate_limit._redis_probed = False

    fake_store: dict[str, str] = {}

    class FakeRedis:
        async def ping(self): return True
        async def set(self, key, val, nx=False, ex=None):
            if nx and key in fake_store:
                return None
            fake_store[key] = val
            return True
        async def delete(self, key):
            fake_store.pop(key, None)

    with patch.object(
        notification_rate_limit, "get_settings",
        return_value=SimpleNamespace(redis_url="redis://x"),
    ):
        # First call → SET NX returns truthy → helper says "don't skip"
        async def _fake_from_url(*args, **kwargs):
            return FakeRedis()
        with patch("redis.asyncio.from_url", side_effect=lambda *a, **k: FakeRedis()):
            parent_id = str(uuid.uuid4())
            first = await notification_rate_limit.should_skip(
                "photo_shared", parent_id, ttl_seconds=1800,
            )
            second = await notification_rate_limit.should_skip(
                "photo_shared", parent_id, ttl_seconds=1800,
            )

    assert first is False, "first call should proceed"
    assert second is True, "second call within TTL should skip"


@pytest.mark.asyncio
async def test_throttle_scopes_by_reference_id():
    """Different reference_ids live under separate keys — an overdue
    reminder on invoice A doesn't block the reminder on invoice B."""
    notification_rate_limit._redis_client = None
    notification_rate_limit._redis_probed = False

    fake_store: dict[str, str] = {}

    class FakeRedis:
        async def ping(self): return True
        async def set(self, key, val, nx=False, ex=None):
            if nx and key in fake_store:
                return None
            fake_store[key] = val
            return True
        async def delete(self, key):
            fake_store.pop(key, None)

    parent_id = str(uuid.uuid4())
    with patch.object(
        notification_rate_limit, "get_settings",
        return_value=SimpleNamespace(redis_url="redis://x"),
    ):
        with patch("redis.asyncio.from_url", side_effect=lambda *a, **k: FakeRedis()):
            a_first = await notification_rate_limit.should_skip(
                "invoice_overdue", parent_id,
                ttl_seconds=7 * 24 * 3600, reference_id="invoice-A",
            )
            b_first = await notification_rate_limit.should_skip(
                "invoice_overdue", parent_id,
                ttl_seconds=7 * 24 * 3600, reference_id="invoice-B",
            )
            a_second = await notification_rate_limit.should_skip(
                "invoice_overdue", parent_id,
                ttl_seconds=7 * 24 * 3600, reference_id="invoice-A",
            )

    assert a_first is False and b_first is False
    assert a_second is True, "same invoice second call should skip"
