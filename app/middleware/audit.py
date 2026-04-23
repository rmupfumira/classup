"""Audit middleware — records user actions per the configured audit level.

Runs after the auth middleware so tenant_id / user_id / role context is
already set. Writes an AuditLog row for each qualifying request.
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.models import AuditLevel
from app.utils.tenant_context import (
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id_or_none,
)

logger = logging.getLogger(__name__)

# Paths we never audit (too noisy / static / healthchecks)
NEVER_AUDIT_EXACT = {
    "/health",
    "/favicon.ico",
    "/robots.txt",
}
NEVER_AUDIT_PREFIXES = (
    "/static/",
    "/favicon",
    # Polling endpoints used by the audit viewer itself — would cause recursion
    # of events-for-loading-events.
    "/api/v1/admin/audit-events",
    "/api/v1/admin/online-users",
)

# Auth endpoints always audited at MINIMAL
AUTH_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/register",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/me/password",
    "/login",
    "/logout",
    "/register",
    "/register/teacher",
    "/forgot-password",
    "/reset-password",
}

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class AuditMiddleware(BaseHTTPMiddleware):
    """Write an audit event for each qualifying request."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        method = request.method

        # Pre-filter obvious no-ops before doing any DB work
        if (
            path in NEVER_AUDIT_EXACT
            or any(path.startswith(p) for p in NEVER_AUDIT_PREFIXES)
        ):
            return await call_next(request)

        response = await call_next(request)

        try:
            await self._maybe_audit(request, response, path, method)
        except Exception:
            # Audit must never break the response
            logger.exception("Audit middleware failed to record event")

        return response

    async def _maybe_audit(
        self, request: Request, response: Response, path: str, method: str
    ) -> None:
        """Decide whether to log this request and, if so, do it."""
        # Lazy imports to avoid circular dependencies at app boot
        from app.database import get_db_context
        from app.services.audit_service import get_audit_service

        service = get_audit_service()

        # Get config with its own session — fast, indexed lookup
        async with get_db_context() as cfg_db:
            config = await service.get_config(cfg_db)

        if not config.get("enabled", True):
            return

        level = config.get("level", AuditLevel.STANDARD.value)
        status_code = getattr(response, "status_code", 0)

        is_auth_event = path in AUTH_PATHS
        is_admin_path = path.startswith("/api/v1/admin") or path.startswith("/admin")
        is_permission_denied = status_code in {401, 402, 403, 429}
        is_write = method in WRITE_METHODS

        should_log = False

        # MINIMAL: auth + permission-denied + all super admin writes
        if is_auth_event or is_permission_denied:
            should_log = True
        elif is_admin_path and is_write:
            should_log = True

        if not should_log and level in (AuditLevel.STANDARD.value, AuditLevel.VERBOSE.value):
            if is_write:
                should_log = True

        if not should_log and level == AuditLevel.VERBOSE.value:
            # Reads — filter out API health/plans/public and static
            if not path.startswith(("/api/v1/plans", "/api/v1/paystack")):
                should_log = True

        if not should_log:
            return

        # Collect request metadata
        user_id = get_current_user_id_or_none()
        tenant_id = get_tenant_id_or_none()
        role = get_current_user_role()
        ip = self._client_ip(request)
        ua = request.headers.get("user-agent")

        # Derive the action name from path + method
        action = self._action_for(path, method, status_code, is_auth_event)

        # Look up denormalised user / tenant names (best-effort)
        user_email = user_name = tenant_name = None
        if user_id or tenant_id:
            try:
                from app.models import Tenant, User
                async with get_db_context() as db:
                    if user_id:
                        u = await db.get(User, user_id)
                        if u:
                            user_email = u.email
                            user_name = f"{u.first_name} {u.last_name}".strip()
                    if tenant_id:
                        t = await db.get(Tenant, tenant_id)
                        if t:
                            tenant_name = t.name
            except Exception:
                logger.debug("Audit: failed to load user/tenant for context", exc_info=True)

        # Query-string summary (trimmed)
        qs = str(request.url.query) if request.url.query else None
        details: dict = {}
        if qs:
            details["query"] = qs[:500]

        async with get_db_context() as write_db:
            await service.log_event(
                write_db,
                action=action,
                user_id=user_id,
                user_email=user_email,
                user_name=user_name,
                user_role=role,
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                method=method,
                path=path[:500],
                status_code=status_code,
                ip_address=ip,
                user_agent=ua,
                details=details or None,
            )
            await write_db.commit()

    @staticmethod
    def _client_ip(request: Request) -> str | None:
        # Trust X-Forwarded-For first (Railway proxies)
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()[:45]
        if request.client:
            return request.client.host[:45]
        return None

    @staticmethod
    def _action_for(path: str, method: str, status: int, is_auth: bool) -> str:
        """Produce a short, normalised action key from method + path."""
        if is_auth:
            if "login" in path:
                return "auth.login.failed" if status >= 400 else "auth.login"
            if "logout" in path:
                return "auth.logout"
            if "register" in path:
                return "auth.register"
            if "forgot-password" in path:
                return "auth.forgot_password"
            if "reset-password" in path:
                return "auth.reset_password"
            if "/me/password" in path:
                return "auth.change_password"

        # Map /api/v1/students/{id}/edit -> students, /api/v1/admin/tenants -> admin.tenants
        segments = [s for s in path.strip("/").split("/") if s and s not in ("api", "v1")]
        if not segments:
            segments = ["root"]
        # Replace UUID-like segments with ":id"
        cleaned = []
        import re
        uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
        for s in segments:
            if uuid_re.match(s):
                cleaned.append(":id")
                break  # don't go past the first ID
            else:
                cleaned.append(s.replace("-", "_"))

        verb_map = {
            "GET": "view",
            "POST": "create",
            "PUT": "update",
            "PATCH": "update",
            "DELETE": "delete",
        }
        verb = verb_map.get(method, method.lower())
        base = ".".join(cleaned[:3])
        suffix = "" if status < 400 else ".denied"
        return f"{base}.{verb}{suffix}"[:80]
