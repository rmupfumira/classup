"""MIS & Academic Analytics web routes — Phase 1 Executive Dashboard."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import FeatureLockedException
from app.models.tenant import Tenant
from app.services.auth_service import get_auth_service
from app.templates_config import templates
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id,
)

router = APIRouter(prefix="/mis", tags=["MIS"])


def _require_auth(request: Request):
    user_id = get_current_user_id_or_none()
    if not user_id:
        next_url = request.url.path
        if request.url.query:
            next_url += "?" + request.url.query
        return RedirectResponse(
            url=f"/login?next={quote(next_url, safe='/?&=')}",
            status_code=302,
        )
    return None


@router.get("", response_class=HTMLResponse)
async def mis_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Executive Dashboard — the landing page of the MIS module.

    Feature-gated: redirects to /subscription?locked=mis when the
    tenant's plan or settings don't include the mis feature. Keeps
    the Phase-1 slice from leaking into tenants on starter plans.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect
    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN", "TEACHER"):
        return RedirectResponse(url="/dashboard", status_code=302)

    user = await get_auth_service().get_current_user(db, get_current_user_id_or_none())
    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id) if tenant_id else None
    features = (tenant.settings or {}).get("features", {}) if tenant else {}
    if not features.get("mis"):
        return RedirectResponse(url="/subscription?locked=mis", status_code=302)

    return templates.TemplateResponse(
        "mis/dashboard.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "current_language": get_current_language(),
        },
    )
