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


async def _mis_guard(request: Request, db: AsyncSession):
    """Shared auth + feature gate for every MIS page.

    Returns either a RedirectResponse or a (user, tenant) tuple so the
    caller can build its template context directly.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect, None
    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN", "TEACHER"):
        return RedirectResponse(url="/dashboard", status_code=302), None

    user = await get_auth_service().get_current_user(db, get_current_user_id_or_none())
    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id) if tenant_id else None
    features = (tenant.settings or {}).get("features", {}) if tenant else {}
    if not features.get("mis"):
        return RedirectResponse(url="/subscription?locked=mis", status_code=302), None
    return None, (user, tenant)


async def _mis_class_context(request: Request, db: AsyncSession) -> dict:
    """Teachers only get analytics for classes they're class teacher of,
    so their pages render that list (default: the selected class) instead
    of the school-wide picker. Admins get nothing extra."""
    if get_current_user_role() != "TEACHER":
        return {"mis_teacher": False, "mis_classes": [], "mis_default_class_id": None}
    from app.web.helpers import get_teacher_class_context

    ctx = await get_teacher_class_context(request, db)
    general = ctx.get("general_class_ids") or set()
    classes = [c for c in ctx.get("teacher_classes") or [] if c.id in general]
    selected = ctx.get("selected_class")
    default = selected.id if selected is not None and selected.id in general else (
        classes[0].id if classes else None
    )
    return {"mis_teacher": True, "mis_classes": classes, "mis_default_class_id": default}


@router.get("", response_class=HTMLResponse)
async def mis_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Executive Dashboard — the landing page of the MIS module."""
    redirect, ctx = await _mis_guard(request, db)
    if redirect:
        return redirect
    user, tenant = ctx
    return templates.TemplateResponse(
        "mis/dashboard.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "current_language": get_current_language(),
            **(await _mis_class_context(request, db)),
        },
    )


@router.get("/subjects", response_class=HTMLResponse)
async def mis_subjects(request: Request, db: AsyncSession = Depends(get_db)):
    """Phase 2 — Subject Performance Analysis."""
    redirect, ctx = await _mis_guard(request, db)
    if redirect:
        return redirect
    user, tenant = ctx
    return templates.TemplateResponse(
        "mis/subjects.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "current_language": get_current_language(),
            **(await _mis_class_context(request, db)),
        },
    )


@router.get("/distribution", response_class=HTMLResponse)
async def mis_distribution(request: Request, db: AsyncSession = Depends(get_db)):
    """Phase 3 — Grade Distribution charts."""
    redirect, ctx = await _mis_guard(request, db)
    if redirect:
        return redirect
    user, tenant = ctx
    return templates.TemplateResponse(
        "mis/distribution.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "current_language": get_current_language(),
            **(await _mis_class_context(request, db)),
        },
    )


@router.get("/merit-lists", response_class=HTMLResponse)
async def mis_merit_lists(request: Request, db: AsyncSession = Depends(get_db)):
    """Phase 3 — Merit lists / rankings."""
    redirect, ctx = await _mis_guard(request, db)
    if redirect:
        return redirect
    user, tenant = ctx
    return templates.TemplateResponse(
        "mis/merit_lists.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "current_language": get_current_language(),
            **(await _mis_class_context(request, db)),
        },
    )


@router.get("/longitudinal", response_class=HTMLResponse)
async def mis_longitudinal(request: Request, db: AsyncSession = Depends(get_db)):
    """Phase 4 — Longitudinal performance for one student."""
    redirect, ctx = await _mis_guard(request, db)
    if redirect:
        return redirect
    user, tenant = ctx
    return templates.TemplateResponse(
        "mis/longitudinal.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "current_language": get_current_language(),
            **(await _mis_class_context(request, db)),
        },
    )


@router.get("/early-warning", response_class=HTMLResponse)
async def mis_early_warning(request: Request, db: AsyncSession = Depends(get_db)):
    """Phase 5 — Early Warning rules + at-risk scan."""
    redirect, ctx = await _mis_guard(request, db)
    if redirect:
        return redirect
    user, tenant = ctx
    return templates.TemplateResponse(
        "mis/early_warning.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "current_language": get_current_language(),
            **(await _mis_class_context(request, db)),
        },
    )
