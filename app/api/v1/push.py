"""Web Push HTTP surface — subscribe, unsubscribe, fetch VAPID key, test.

Endpoints:
  GET  /public-key   — what the browser passes as applicationServerKey
  POST /subscribe    — store/upsert a browser's pushManager subscription
  POST /unsubscribe  — remove a device's subscription
  POST /test         — send a hello-world push to all of the caller's devices

Endpoints don't require any feature flag — push is a platform capability,
not a paid feature. Per-tenant push enable/disable is handled at the UI
level (the Notifications settings page lets the user opt in or out).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.schemas.common import APIResponse
from app.services.auth_service import get_auth_service
from app.services.push_service import (
    delete_subscription_by_endpoint,
    get_vapid_config,
    list_user_subscriptions,
    send_to_user,
    upsert_subscription,
)
from app.utils.tenant_context import (
    get_current_user_id_or_none,
    get_tenant_id_or_none,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["Push Notifications"])


# ============================================================================
# Schemas
# ============================================================================

class SubscriptionKeys(BaseModel):
    """The `keys` block of a PushSubscription.toJSON()."""
    p256dh: str = Field(..., min_length=1)
    auth: str = Field(..., min_length=1)


class SubscribeIn(BaseModel):
    """Mirror of PushSubscription.toJSON() that the browser sends.

    Note: pushManager.subscribe() returns an object whose toJSON() shape is
    `{ endpoint, expirationTime, keys: { p256dh, auth } }`. We only need
    endpoint + keys.
    """
    endpoint: str = Field(..., min_length=1, max_length=2000)
    keys: SubscriptionKeys
    # The browser may pass an `expirationTime` field; we ignore it.


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=2000)


class SubscriptionRow(BaseModel):
    """What the Notifications settings page renders for each device."""
    id: str
    endpoint_preview: str  # first 60 chars; full endpoint is huge + boring
    user_agent: str | None
    last_seen_at: str | None
    last_failed_at: str | None
    last_error: str | None


# ============================================================================
# Helpers
# ============================================================================

async def _current_user(db: AsyncSession) -> User:
    """Look up the caller. 401 if not signed in."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await get_auth_service().get_current_user(db, user_id)
    except Exception as e:
        logger.warning(f"Push auth lookup failed: {e}")
        raise HTTPException(status_code=401, detail="Not authenticated")


def _preview_endpoint(endpoint: str) -> str:
    """Trim endpoint for display — full URL is 200+ chars and unhelpful."""
    if not endpoint:
        return ""
    if len(endpoint) <= 60:
        return endpoint
    return endpoint[:50] + "..." + endpoint[-7:]


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/public-key", response_model=APIResponse[dict])
async def public_key(db: AsyncSession = Depends(get_db)):
    """Browser needs this to call pushManager.subscribe().

    Returns:
        {
          "key":        "BNc..."  (base64url public key) — empty if unconfigured
          "configured": true | false
          "subject":    "mailto:..." (informational only)
        }

    Open to any authenticated user — they need it to subscribe. Unconfigured
    state surfaces in the UI as "Push not set up by administrator."
    """
    await _current_user(db)
    config = await get_vapid_config(db)
    return APIResponse(
        data={
            "key": config.public_key_b64url,
            "configured": config.configured,
            "subject": config.subject if config.configured else "",
        }
    )


@router.post("/subscribe", response_model=APIResponse[dict])
async def subscribe(
    body: SubscribeIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Store a browser's push subscription so we can send to it later.

    Idempotent: re-subscribing from the same device updates the row instead
    of creating a duplicate (endpoint is UNIQUE).
    """
    user = await _current_user(db)
    tenant_id = get_tenant_id_or_none()
    if not tenant_id:
        # SUPER_ADMIN has no tenant — they can still subscribe so they get
        # platform notifications. We use a sentinel tenant_id? No — better
        # to reject and tell them to use a tenant context.
        raise HTTPException(
            status_code=400,
            detail="Cannot subscribe without a tenant context.",
        )

    ua = request.headers.get("user-agent", "")[:500] or None

    sub = await upsert_subscription(
        db,
        tenant_id=tenant_id,
        user_id=user.id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=ua,
    )
    await db.commit()
    logger.info(f"Push subscription stored: user={user.id} sub={sub.id}")
    return APIResponse(
        data={"id": str(sub.id), "endpoint_preview": _preview_endpoint(sub.endpoint)},
        message="Notifications enabled on this device.",
    )


@router.post("/unsubscribe", response_model=APIResponse[dict])
async def unsubscribe(
    body: UnsubscribeIn,
    db: AsyncSession = Depends(get_db),
):
    """Forget a device's subscription. Called from the Notifications settings
    page when the user toggles off — also from the browser side as a
    pushManager.unsubscribe() follow-up."""
    user = await _current_user(db)
    removed = await delete_subscription_by_endpoint(
        db, user_id=user.id, endpoint=body.endpoint
    )
    await db.commit()
    return APIResponse(
        data={"removed": removed},
        message=("Notifications disabled on this device." if removed
                 else "No matching subscription found."),
    )


@router.post("/test", response_model=APIResponse[dict])
async def send_test(db: AsyncSession = Depends(get_db)):
    """Send a hello-world push to every device the caller has subscribed.

    Used by the "Send test notification" button on the Notifications
    settings page so the user can verify end-to-end delivery before
    relying on it for real events.
    """
    user = await _current_user(db)

    config = await get_vapid_config(db)
    if not config.configured:
        raise HTTPException(
            status_code=503,
            detail="Push notifications are not configured on the server.",
        )

    payload = {
        "title": "ClassUp test notification",
        "body": "If you can see this, push notifications are working on this device.",
        "url": "/dashboard",
        "tag": "classup-test",
    }
    result = await send_to_user(db, user.id, payload)
    await db.commit()

    if result.sent == 0:
        raise HTTPException(
            status_code=404,
            detail=(
                "No active subscriptions found for this user. "
                "Enable notifications on a device first."
            ),
        )

    return APIResponse(
        data={
            "sent": result.sent,
            "deleted_dead": result.deleted_dead,
            "failed": result.failed,
        },
        message=f"Sent to {result.sent} device(s).",
    )


@router.get("/subscriptions", response_model=APIResponse[list[SubscriptionRow]])
async def my_subscriptions(db: AsyncSession = Depends(get_db)):
    """List the caller's active push subscriptions — one row per device.

    Powers the "Active devices" list on the Notifications settings page so
    users can see what's enrolled and revoke specific devices.
    """
    user = await _current_user(db)
    subs = await list_user_subscriptions(db, user.id)
    return APIResponse(
        data=[
            SubscriptionRow(
                id=str(s.id),
                endpoint_preview=_preview_endpoint(s.endpoint),
                user_agent=s.user_agent,
                last_seen_at=s.last_seen_at.isoformat() if s.last_seen_at else None,
                last_failed_at=s.last_failed_at.isoformat() if s.last_failed_at else None,
                last_error=s.last_error,
            )
            for s in subs
        ]
    )
