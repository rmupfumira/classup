"""Authentication middleware for JWT token validation."""

import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.security import decode_access_token
from app.utils.tenant_context import (
    clear_all_context,
    set_current_user_id,
    set_current_user_role,
    set_tenant_id,
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that extracts and validates JWT tokens from requests.

    Supports both cookie-based auth (for web) and Authorization header (for API).
    """

    # Paths that don't require authentication
    EXEMPT_PATHS = {
        "/",
        "/login",
        "/register",
        "/forgot-password",
        "/reset-password",
        "/health",
        "/api/docs",
        "/api/redoc",
        "/api/openapi.json",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/invitations/verify",
        "/api/v1/whatsapp/webhook",
    }

    # Path prefixes that don't require authentication
    EXEMPT_PREFIXES = {
        "/static/",
        "/favicon",
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and extract authentication context."""
        # Clear context from previous request
        clear_all_context()

        # Check if path is exempt from auth
        path = request.url.path
        if self._is_exempt_path(path):
            return await call_next(request)

        # Try to extract token from cookie or Authorization header
        token = self._extract_token(request)

        if token:
            # Decode and validate token
            payload = decode_access_token(token)
            if payload:
                # Set context variables
                try:
                    user_id = uuid.UUID(payload["sub"])
                    set_current_user_id(user_id)

                    if payload.get("tenant_id"):
                        tenant_id = uuid.UUID(payload["tenant_id"])
                        set_tenant_id(tenant_id)

                    if payload.get("role"):
                        set_current_user_role(payload["role"])
                except (ValueError, TypeError):
                    # Invalid UUID format - context will remain unset
                    pass

        response = await call_next(request)

        # Clear context after request
        clear_all_context()

        return response

    def _is_exempt_path(self, path: str) -> bool:
        """Check if the path is exempt from authentication."""
        if path in self.EXEMPT_PATHS:
            return True

        for prefix in self.EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return True

        return False

    def _extract_token(self, request: Request) -> str | None:
        """Extract JWT token from request.

        Priority:
        1. Authorization header (Bearer token)
        2. access_token cookie

        Returns:
            Token string or None if not found
        """
        # Try Authorization header first (for API clients)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header.removeprefix("Bearer ").strip()

        # Try cookie (for web clients)
        return request.cookies.get("access_token")
