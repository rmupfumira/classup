"""Redis-backed throttle for parent notifications.

Purpose: prevent a burst of events (many photos in a morning, a 1-day
overdue-reminder interval set by an admin) from firing a corresponding
burst of WhatsApps at the parent. Each call is keyed by (event, parent,
optional reference) and asks Redis "has this fired in the last N
seconds for this parent?" — if yes, skip; if no, mark it + proceed.

Failure modes:
  - No Redis configured → the throttle never trips (fail-open). Parents
    don't lose notifications because of infra; operators see a startup
    warning in bot_session_store's probe and pick that up separately.
  - Redis down at call time → fail-open, log at WARNING. We'd rather
    over-notify by one burst than lose the signal entirely.

Shape of key: ``wa-rate:{event}:{parent_id}[:{ref_id}]`` with a TTL.

2026-11-18 wiring:
  - photo_shared / document_shared → debounce 30 min per parent.
  - invoice_overdue → cap WhatsApp at 7 days per invoice per parent,
    regardless of admin's email cadence setting.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: Any | None = None
_redis_probed: bool = False


async def _get_redis():
    """Lazy singleton. Returns None if Redis isn't configured or down —
    callers treat that as "throttle disabled"."""
    global _redis_client, _redis_probed
    if _redis_probed:
        return _redis_client
    _redis_probed = True
    settings = get_settings()
    if not settings.redis_url:
        logger.info(
            "notification_rate_limit: redis_url not set — throttle disabled (fail-open)"
        )
        return None
    try:
        import redis.asyncio as aioredis  # type: ignore
        _redis_client = aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True,
        )
        await _redis_client.ping()
    except Exception:
        logger.exception(
            "notification_rate_limit: could not connect to Redis — throttle disabled"
        )
        _redis_client = None
    return _redis_client


async def should_skip(
    event: str,
    parent_id: Any,
    *,
    ttl_seconds: int,
    reference_id: Any | None = None,
) -> bool:
    """Return True if the caller should SKIP this notification because
    a matching one fired within the TTL window.

    Return False if it should fire — and in that case the function has
    already marked the key so the next call within TTL will skip.

    ``event`` is a short slug like ``photo_shared`` or ``invoice_overdue``.
    ``parent_id`` is the user id (UUID or str).
    ``reference_id`` scopes the throttle further (e.g. per invoice).
    """
    try:
        r = await _get_redis()
        if r is None:
            return False  # Fail-open — no infra, no throttle.
        key = f"wa-rate:{event}:{parent_id}"
        if reference_id is not None:
            key = f"{key}:{reference_id}"
        # SET NX EX — atomically set if missing, with expiry. Returns
        # truthy on first set, None if the key already existed.
        ok = await r.set(key, "1", nx=True, ex=ttl_seconds)
        if ok:
            return False  # First send in this window — fire away.
        logger.info(
            "notification_rate_limit: skipping %s for parent=%s ref=%s (in window)",
            event, parent_id, reference_id,
        )
        return True
    except Exception:
        # Any other failure is fail-open.
        logger.exception(
            "notification_rate_limit: unexpected error — fail-open for %s parent=%s",
            event, parent_id,
        )
        return False


async def _reset_for_tests(event: str, parent_id: Any, reference_id: Any | None = None) -> None:
    """Clear a throttle key — used by the test suite to make the next
    call in the test fire regardless of prior ones."""
    try:
        r = await _get_redis()
        if r is None:
            return
        key = f"wa-rate:{event}:{parent_id}"
        if reference_id is not None:
            key = f"{key}:{reference_id}"
        await r.delete(key)
    except Exception:
        logger.exception("notification_rate_limit: reset failed")
