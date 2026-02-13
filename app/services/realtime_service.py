"""Real-time WebSocket service with Redis pub/sub for multi-instance support."""

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ConnectionManager:
    """
    Manages WebSocket connections with optional Redis pub/sub for multi-instance deployment.

    For single instance deployment (no Redis), connections are managed in-memory.
    For multi-instance deployment, Redis pub/sub is used to broadcast events.
    """

    def __init__(self):
        """Initialize the connection manager."""
        # In-memory connection storage: {tenant_id:user_id: [websocket, ...]}
        self.active_connections: dict[str, list[WebSocket]] = {}
        # Redis client for pub/sub (initialized lazily)
        self._redis = None
        self._pubsub_task = None

    async def _get_redis(self):
        """Get or create Redis client."""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                )
                # Start pub/sub listener
                self._pubsub_task = asyncio.create_task(self._listen_pubsub())
            except Exception as e:
                logger.warning(f"Redis connection failed, running without pub/sub: {e}")
        return self._redis

    async def _listen_pubsub(self):
        """Listen for pub/sub messages and deliver to local connections."""
        if not self._redis:
            return

        pubsub = self._redis.pubsub()
        try:
            # Subscribe to tenant-specific channels
            await pubsub.psubscribe("ws:*")

            async for message in pubsub.listen():
                if message["type"] == "pmessage":
                    channel = message["channel"]
                    data = json.loads(message["data"])
                    await self._deliver_local(channel, data)
        except Exception as e:
            logger.error(f"Pub/sub listener error: {e}")
        finally:
            await pubsub.close()

    async def _deliver_local(self, channel: str, data: dict):
        """Deliver a message to local connections matching the channel."""
        # Parse channel format: ws:{tenant_id}:{user_id} or ws:tenant:{tenant_id}
        parts = channel.split(":")

        if len(parts) == 3 and parts[1] != "tenant":
            # User-specific: ws:{tenant_id}:{user_id}
            key = f"{parts[1]}:{parts[2]}"
            connections = self.active_connections.get(key, [])
        elif len(parts) == 3 and parts[1] == "tenant":
            # Tenant-wide: ws:tenant:{tenant_id}
            tenant_id = parts[2]
            connections = []
            for key, conns in self.active_connections.items():
                if key.startswith(f"{tenant_id}:"):
                    connections.extend(conns)
        else:
            return

        for ws in connections:
            try:
                await ws.send_json(data)
            except Exception as e:
                logger.debug(f"Failed to send to websocket: {e}")

    def _get_key(self, tenant_id: str, user_id: str) -> str:
        """Generate a connection key."""
        return f"{tenant_id}:{user_id}"

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        tenant_id: str,
    ):
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        key = self._get_key(tenant_id, user_id)

        if key not in self.active_connections:
            self.active_connections[key] = []
        self.active_connections[key].append(websocket)

        logger.info(f"WebSocket connected: {key}")

        # Initialize Redis if available
        await self._get_redis()

    async def disconnect(
        self,
        websocket: WebSocket,
        user_id: str,
        tenant_id: str,
    ):
        """Remove a WebSocket connection."""
        key = self._get_key(tenant_id, user_id)

        if key in self.active_connections:
            try:
                self.active_connections[key].remove(websocket)
                if not self.active_connections[key]:
                    del self.active_connections[key]
            except ValueError:
                pass

        logger.info(f"WebSocket disconnected: {key}")

    async def send_to_user(
        self,
        user_id: str,
        tenant_id: str,
        event: dict[str, Any],
    ):
        """Send an event to a specific user (all their connected sessions)."""
        key = self._get_key(tenant_id, user_id)

        # Try Redis pub/sub first for multi-instance support
        redis = await self._get_redis()
        if redis:
            try:
                await redis.publish(f"ws:{key}", json.dumps(event))
                return
            except Exception as e:
                logger.warning(f"Redis publish failed, falling back to local: {e}")

        # Fallback to local delivery
        connections = self.active_connections.get(key, [])
        for ws in connections:
            try:
                await ws.send_json(event)
            except Exception as e:
                logger.debug(f"Failed to send to websocket: {e}")

    async def broadcast_to_tenant(
        self,
        tenant_id: str,
        event: dict[str, Any],
    ):
        """Send an event to all connected users in a tenant."""
        # Try Redis pub/sub first
        redis = await self._get_redis()
        if redis:
            try:
                await redis.publish(f"ws:tenant:{tenant_id}", json.dumps(event))
                return
            except Exception as e:
                logger.warning(f"Redis publish failed, falling back to local: {e}")

        # Fallback to local delivery
        for key, connections in self.active_connections.items():
            if key.startswith(f"{tenant_id}:"):
                for ws in connections:
                    try:
                        await ws.send_json(event)
                    except Exception as e:
                        logger.debug(f"Failed to send to websocket: {e}")

    async def broadcast_to_users(
        self,
        user_ids: list[str],
        tenant_id: str,
        event: dict[str, Any],
    ):
        """Send an event to multiple specific users."""
        for user_id in user_ids:
            await self.send_to_user(user_id, tenant_id, event)

    # ============== Event Type Helpers ==============

    async def send_notification(
        self,
        user_id: str,
        tenant_id: str,
        notification: dict[str, Any],
    ):
        """Send a notification event to a user."""
        await self.send_to_user(user_id, tenant_id, {
            "type": "notification",
            "data": notification,
        })

    async def send_unread_count(
        self,
        user_id: str,
        tenant_id: str,
        messages: int,
        notifications: int,
    ):
        """Send updated unread counts to a user."""
        await self.send_to_user(user_id, tenant_id, {
            "type": "unread_count",
            "data": {
                "messages": messages,
                "notifications": notifications,
            },
        })

    async def send_message_received(
        self,
        user_id: str,
        tenant_id: str,
        message_data: dict[str, Any],
    ):
        """Send a message received event."""
        await self.send_to_user(user_id, tenant_id, {
            "type": "message_received",
            "data": message_data,
        })

    async def send_attendance_update(
        self,
        user_ids: list[str],
        tenant_id: str,
        attendance_data: dict[str, Any],
    ):
        """Send attendance update to multiple users."""
        await self.broadcast_to_users(user_ids, tenant_id, {
            "type": "attendance_update",
            "data": attendance_data,
        })


# Singleton instance
_connection_manager: ConnectionManager | None = None


def get_connection_manager() -> ConnectionManager:
    """Get the connection manager singleton."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager
