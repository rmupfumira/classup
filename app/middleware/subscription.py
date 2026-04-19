"""Subscription enforcement middleware.

Checks if a tenant's subscription is active and redirects/blocks
if it's expired, suspended, or past due.
"""

import logging
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.tenant_context import get_tenant_id_or_none, get_current_user_role

logger = logging.getLogger(__name__)

# Paths that bypass subscription checks
EXEMPT_PATHS = {
    "/",
    "/login",
    "/register",
    "/logout",
    "/forgot-password",
    "/reset-password",
    "/profile",
    "/health",
    "/subscription",
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/logout",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/invitations/verify",
    "/api/v1/whatsapp/webhook",
    "/api/v1/paystack/webhook",
}

EXEMPT_PREFIXES = (
    "/static/",
    "/favicon",
    "/admin",           # Super admin pages always accessible
    "/api/v1/admin",    # Super admin API always accessible
    "/api/v1/auth/me",  # Profile + change-password — always accessible
    "/api/v1/subscription",  # Subscription management endpoints
    "/api/v1/plans",    # Public plans listing
    "/api/v1/paystack", # Paystack webhooks
)


class SubscriptionMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces active subscription for tenant-scoped requests."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Skip exempt paths
        if path in EXEMPT_PATHS or any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        # Only check tenant-scoped users (not SUPER_ADMIN, not unauthenticated)
        role = get_current_user_role()
        tenant_id = get_tenant_id_or_none()

        if not tenant_id or not role or role == "SUPER_ADMIN":
            return await call_next(request)

        # Check subscription status (lazy import to avoid circular deps)
        from app.database import get_db_context
        from app.services.subscription_service import get_subscription_service

        service = get_subscription_service()
        try:
            async with get_db_context() as db:
                sub = await service.get_tenant_subscription(db, tenant_id)
                # No subscription record → auto-enroll in free trial
                if sub is None:
                    try:
                        sub = await service.auto_enroll_trial(db, tenant_id)
                        await db.commit()
                    except Exception:
                        logger.exception("Failed to auto-enroll tenant in trial")
                        return await call_next(request)
                is_active = await service.is_subscription_active(db, tenant_id)
        except Exception:
            # If we can't check, let the request through (fail open)
            logger.exception("Failed to check subscription status")
            return await call_next(request)

        if is_active:
            return await call_next(request)

        # Subscription not active — block access
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=402,
                content={
                    "status": "error",
                    "message": "Your subscription is inactive. Please update your payment method.",
                    "data": {"redirect_url": "/subscription"},
                },
            )
        else:
            # Web request — redirect to subscription page
            return RedirectResponse(url="/subscription", status_code=302)
