"""WebSocket API endpoint for real-time updates."""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.realtime_service import get_connection_manager
from app.utils.security import decode_jwt

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    """
    WebSocket endpoint for real-time updates.

    The token is passed in the URL path for authentication since
    WebSocket connections don't support headers in all browsers.
    """
    # Validate the JWT token
    payload = decode_jwt(token)
    if not payload:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")

    if not user_id:
        await websocket.close(code=4001, reason="Invalid token payload")
        return

    # Super admins don't have a tenant_id
    if not tenant_id:
        tenant_id = "super_admin"

    manager = get_connection_manager()

    try:
        await manager.connect(websocket, user_id, tenant_id)

        # Send initial connection success message
        await websocket.send_json({
            "type": "connected",
            "data": {
                "message": "WebSocket connection established",
                "user_id": user_id,
            },
        })

        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Wait for incoming messages (ping/pong, client events, etc.)
                data = await websocket.receive_json()

                # Handle client-side events
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif data.get("type") == "mark_read":
                    # Client can send events to mark notifications as read, etc.
                    # These would be processed here
                    pass

            except Exception as e:
                # JSON decode error or other receive error
                logger.debug(f"WebSocket receive error: {e}")
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {user_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await manager.disconnect(websocket, user_id, tenant_id)
