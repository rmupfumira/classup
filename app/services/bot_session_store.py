"""Per-user WhatsApp bot conversation state — Redis-backed with 24hr TTL.

Why Redis and not the DB:
  - The WhatsApp session window is 24h; conversation memory beyond that
    can't be free-form-replied to anyway, so anything longer would just
    burn disk.
  - Sub-millisecond reads on every inbound message → in the hot path.
  - Ephemeral by nature — we never need to backfill or query this history,
    so a persistent table adds cost with no upside.

Fallback: if Redis is unreachable or not configured, the store returns
an empty history and silently drops writes. The AI loop degrades to
single-turn mode (still functional; just no memory of prior messages).
That's better than crashing the webhook.

Storage shape per key ``whatsapp:bot:{user_id}``:

    [
      {"role": "user",      "content": "..."},
      {"role": "assistant", "content": [...blocks...]},
      ...
    ]

Rolling window: keep only the last N turns (default 10) — every append
trims from the head.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# 24 hours — matches WhatsApp's free-form-reply window; beyond it we'd
# have to send a template anyway, which is the app's job, not the bot's.
SESSION_TTL_SECONDS = 24 * 60 * 60

# Any conversation kept in Redis will be truncated to at most this many
# messages (last N wins). Config also honoured on read via
# AIConfig.max_conversation_turns, but this is a hard ceiling on write
# so a broken caller can't blow up the Redis key.
MAX_HISTORY_MESSAGES = 40  # ~20 user/assistant pairs


def _key(user_id: uuid.UUID) -> str:
    return f"whatsapp:bot:{user_id}"


class BotSessionStore:
    """Thin Redis wrapper. Lazy-initialises the client so a missing
    Redis config doesn't crash at import time."""

    def __init__(self) -> None:
        self._redis = None
        self._probed = False

    async def _get_redis(self):
        if self._probed:
            return self._redis
        self._probed = True
        try:
            import redis.asyncio as aioredis  # type: ignore
            if not settings.redis_url:
                logger.warning("redis_url not set — bot sessions will be stateless")
                return None
            self._redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            # Verify the connection with a quick ping so we don't only
            # discover Redis is down deep inside the AI loop.
            await self._redis.ping()
            return self._redis
        except Exception:
            logger.exception("Failed to connect to Redis; bot sessions will be stateless")
            self._redis = None
            return None

    async def get_history(
        self, user_id: uuid.UUID, max_turns: int
    ) -> list[dict[str, Any]]:
        """Return the last ``max_turns`` messages for this user.

        Turns are counted as *messages* (user + assistant + tool_result all
        count) — matches how Anthropic bills. Returns [] if Redis is
        unreachable or no history exists.
        """
        r = await self._get_redis()
        if r is None:
            return []
        try:
            raw = await r.get(_key(user_id))
        except Exception:
            logger.exception("Redis read failed for user %s", user_id)
            return []
        if not raw:
            return []
        try:
            history = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Corrupt bot session for user %s; discarding", user_id)
            return []
        if not isinstance(history, list):
            return []
        return history[-max_turns:] if max_turns > 0 else []

    async def save_history(
        self, user_id: uuid.UUID, history: list[dict[str, Any]]
    ) -> None:
        """Overwrite the user's conversation with ``history`` and reset the
        TTL. Trims to MAX_HISTORY_MESSAGES to bound the Redis key size.
        """
        r = await self._get_redis()
        if r is None:
            return
        trimmed = history[-MAX_HISTORY_MESSAGES:] if history else []
        try:
            await r.set(
                _key(user_id),
                json.dumps(trimmed, default=str),
                ex=SESSION_TTL_SECONDS,
            )
        except Exception:
            logger.exception("Redis write failed for user %s", user_id)

    async def clear(self, user_id: uuid.UUID) -> None:
        """Drop the user's conversation. Called when the user explicitly
        opts out or resets."""
        r = await self._get_redis()
        if r is None:
            return
        try:
            await r.delete(_key(user_id))
        except Exception:
            logger.exception("Redis delete failed for user %s", user_id)


_store: BotSessionStore | None = None


def get_bot_session_store() -> BotSessionStore:
    """Singleton accessor — the store holds a lazy Redis connection so
    keeping one instance per process is the right call."""
    global _store
    if _store is None:
        _store = BotSessionStore()
    return _store
