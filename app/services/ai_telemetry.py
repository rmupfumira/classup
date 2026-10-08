"""AI telemetry — tool-call logging + daily usage caps.

Added 2026-10-08 as part of the Principal AI review hardening.

Two separate concerns:

- ``record_tool_call`` writes one :class:`AIToolCall` row per tool
  invocation (success, dedupe, or error). Best-effort — a logging
  failure must never break the bot.
- ``check_and_increment_user_quota`` / ``check_and_increment_tenant_quota``
  enforce the per-user and per-tenant daily caps that were previously
  defined on ``AIConfig`` but never actually read. INCR-first-then-check
  so a race between parallel inbound messages can at most exceed the
  cap by the number of concurrent workers (acceptable for a soft
  cost control).

Why Redis for the quota counter: counting against the DB on every
inbound would double DB load for a decision we only make once per
message. Counters survive worker restarts; TTL expires them at the
end of the day so there's no sweep job to run.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AIToolCall

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Tool-call telemetry
# ---------------------------------------------------------------------------

async def record_tool_call(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    whatsapp_message_id: str | None,
    tool_name: str,
    args_hash: str,
    outcome: str,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
) -> None:
    """Insert one AIToolCall row. Never raises.

    The caller holds the session; we don't commit here — the AI loop
    commits once at the end of its turn (same pattern as the outbound
    log writer). On any exception, log and swallow so a telemetry
    failure can't take down the bot.
    """
    try:
        row = AIToolCall(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            whatsapp_message_id=whatsapp_message_id,
            tool_name=tool_name[:64],
            args_hash=args_hash[:64],
            outcome=outcome[:32],
            model=model[:64] if model else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
    except Exception:
        logger.exception(
            "Failed to persist ai_tool_calls row for tool=%s user=%s",
            tool_name, user_id,
        )


# ---------------------------------------------------------------------------
# Daily caps (Redis counters)
# ---------------------------------------------------------------------------

# Soft cost-control: once a parent or tenant hits their daily cap, the
# dispatcher falls back to MENU mode for the rest of the day. Resets at
# UTC midnight (the TTL naturally expires the key).

# Cap-key TTL: give an extra 2h past end-of-day so a message sent at
# 23:59 UTC doesn't lose its counter at 00:00 (the day-key for a late
# message would still be "today" at increment time; TTL covers any
# clock skew between app and Redis).
_CAP_KEY_TTL_SECONDS = 26 * 60 * 60


class _QuotaResult:
    """Lightweight namespace for the (allowed, current, cap) tuple."""

    __slots__ = ("allowed", "current", "cap", "scope")

    def __init__(self, allowed: bool, current: int, cap: int, scope: str) -> None:
        self.allowed = allowed
        self.current = current
        self.cap = cap
        self.scope = scope


async def _redis_client():
    """Return a shared Redis client or ``None`` when Redis isn't available.

    Mirrors the fallback stance of :mod:`bot_session_store`: a missing
    Redis makes the quota check a no-op (allow). Operators can see
    this in the logs if they wonder why caps aren't firing.
    """
    try:
        import redis.asyncio as aioredis  # type: ignore
        if not settings.redis_url:
            return None
        # Cheap — same Redis cluster the session store uses; the client
        # lib pools connections internally.
        return aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True,
        )
    except Exception:
        logger.exception("Failed to initialise Redis for quota checks")
        return None


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


async def _incr_and_check(
    key: str, cap: int, scope: str,
) -> _QuotaResult:
    """INCR a Redis counter and compare against cap.

    - ``cap <= 0``: cap disabled → always allow.
    - Redis unavailable → always allow (fail-open to the operator's
      stance — a dead cache shouldn't silently cut parents off).
    - On any Redis error → log + allow.
    """
    if cap is None or cap <= 0:
        return _QuotaResult(allowed=True, current=0, cap=cap or 0, scope=scope)

    client = await _redis_client()
    if client is None:
        return _QuotaResult(allowed=True, current=0, cap=cap, scope=scope)

    try:
        current = int(await client.incr(key))
        # Set TTL only on the first increment to avoid resetting it
        # every message (the key starts at 1 after a fresh INCR).
        if current == 1:
            await client.expire(key, _CAP_KEY_TTL_SECONDS)
        return _QuotaResult(
            allowed=(current <= cap),
            current=current,
            cap=cap,
            scope=scope,
        )
    except Exception:
        logger.exception("Redis quota check failed for key=%s", key)
        return _QuotaResult(allowed=True, current=0, cap=cap, scope=scope)


async def check_and_increment_user_quota(
    user_id: uuid.UUID, cap: int,
) -> _QuotaResult:
    """Allow or deny one more AI message for this user today."""
    key = f"ai:cap:user:{user_id}:{_today_utc()}"
    return await _incr_and_check(key, cap, scope="user")


async def check_and_increment_tenant_quota(
    tenant_id: uuid.UUID, cap: int,
) -> _QuotaResult:
    """Allow or deny one more AI message on this tenant today."""
    key = f"ai:cap:tenant:{tenant_id}:{_today_utc()}"
    return await _incr_and_check(key, cap, scope="tenant")


# ---------------------------------------------------------------------------
# Per-phone webhook rate limit
# ---------------------------------------------------------------------------

# Belt-and-braces against a parent (or a probe) spamming the inbound
# webhook. 30/min is generous for human interaction and still keeps the
# cost of a loop-smashing attacker finite. One phone over the cap gets
# silently dropped (we still 200 to Meta so retries don't fire).

_PHONE_RATE_WINDOW_SECONDS = 60
_PHONE_RATE_KEY_TTL = 90   # window + grace


async def check_phone_rate_limit(
    from_phone: str, max_per_minute: int = 30,
) -> bool:
    """Return True if this phone is UNDER the per-minute cap.

    - Redis unavailable → fail-open (True).
    - Any Redis error → fail-open and log.
    - ``max_per_minute <= 0`` → allow (feature disabled).
    """
    if not from_phone or max_per_minute <= 0:
        return True
    client = await _redis_client()
    if client is None:
        return True

    import time as _time
    window = int(_time.time()) // _PHONE_RATE_WINDOW_SECONDS
    key = f"wa:rate:{from_phone}:{window}"
    try:
        current = int(await client.incr(key))
        if current == 1:
            await client.expire(key, _PHONE_RATE_KEY_TTL)
        return current <= max_per_minute
    except Exception:
        logger.exception("Phone rate-limit check failed for %s", from_phone)
        return True
