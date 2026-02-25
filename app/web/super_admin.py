"""Super Admin web routes for tenant management and platform settings."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models.system_settings import SystemSettings
from app.models.user import Role
from app.services.auth_service import get_auth_service
from app.services.email_service import EMAIL_CONFIG_KEY
from app.services.tenant_service import get_tenant_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
)

router = APIRouter(prefix="/admin")


async def _get_current_user(db: AsyncSession):
    """Get the current user from the database."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    auth_service = get_auth_service()
    try:
        return await auth_service.get_current_user(db, user_id)
    except Exception:
        return None


def _require_super_admin():
    """Check if current user is super admin."""
    role = get_current_user_role()
    if role != Role.SUPER_ADMIN.value:
        raise ForbiddenException("Super admin access required")


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the super admin dashboard."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    _require_super_admin()

    tenant_service = get_tenant_service()
    stats = await tenant_service.get_platform_stats(db)

    return templates.TemplateResponse(
        "super_admin/index.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/tenants", response_class=HTMLResponse)
async def tenants_list(
    request: Request,
    search: str | None = None,
    is_active: bool | None = None,
    education_type: str | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the tenants list page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    _require_super_admin()

    tenant_service = get_tenant_service()
    tenants, total = await tenant_service.get_tenants(
        db,
        is_active=is_active,
        education_type=education_type,
        search=search,
        page=page,
        page_size=20,
    )

    total_pages = (total + 19) // 20

    return templates.TemplateResponse(
        "super_admin/tenants/list.html",
        {
            "request": request,
            "user": user,
            "tenants": tenants,
            "search": search,
            "is_active": is_active,
            "education_type": education_type,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/tenants/create", response_class=HTMLResponse)
async def tenant_create_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the tenant creation form."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    _require_super_admin()

    return templates.TemplateResponse(
        "super_admin/tenants/create.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/tenants/{tenant_id}", response_class=HTMLResponse)
async def tenant_detail(
    request: Request,
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the tenant detail page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    _require_super_admin()

    tenant_service = get_tenant_service()
    tenant = await tenant_service.get_tenant(db, tenant_id)
    stats = await tenant_service.get_tenant_stats(db, tenant_id)
    admins = await tenant_service.get_tenant_admins(db, tenant_id)

    return templates.TemplateResponse(
        "super_admin/tenants/detail.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "stats": stats,
            "admins": admins,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/tenants/{tenant_id}/edit", response_class=HTMLResponse)
async def tenant_edit_form(
    request: Request,
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the tenant edit form."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    _require_super_admin()

    tenant_service = get_tenant_service()
    tenant = await tenant_service.get_tenant(db, tenant_id)

    return templates.TemplateResponse(
        "super_admin/tenants/edit.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/email-settings", response_class=HTMLResponse)
async def email_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the email settings page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    _require_super_admin()

    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == EMAIL_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    email_config = dict(row.value) if row else {}

    # Mask secrets for display
    if email_config.get("smtp_password"):
        email_config["smtp_password"] = "********"
    if email_config.get("resend_api_key"):
        email_config["resend_api_key"] = "********"

    return templates.TemplateResponse(
        "super_admin/email_settings.html",
        {
            "request": request,
            "user": user,
            "email_config": email_config,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )
