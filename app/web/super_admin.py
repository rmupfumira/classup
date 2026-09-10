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

    # Load plan features so the WhatsApp toggles panel can show "Upgrade
    # your plan" vs. "Available" state per feature.
    plan_features: dict = {}
    try:
        from app.services.subscription_service import get_subscription_service
        sub = await get_subscription_service().get_tenant_subscription(db, tenant_id)
        if sub and sub.plan and sub.plan.features:
            plan_features = sub.plan.features
    except Exception:
        pass

    tenant_features = (tenant.settings or {}).get("features", {}) or {}

    # Jurisdiction context — the effective resolution (tenant override →
    # platform default → country registry), plus the raw settings so the
    # UI can distinguish "explicitly set" from "inherited".
    from app.services import jurisdiction_service, platform_service
    platform_defaults = (await platform_service.get_defaults(db)).to_dict()
    effective_jurisdiction = jurisdiction_service.resolve_jurisdiction(
        tenant, platform_defaults=platform_defaults,
    )
    tenant_country_raw = (tenant.settings or {}).get("country") or ""
    tenant_currency_raw = (tenant.settings or {}).get("billing_currency") or ""
    tenant_timezone_raw = (tenant.settings or {}).get("timezone") or ""

    return templates.TemplateResponse(
        "super_admin/tenants/edit.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "tenant_features": tenant_features,
            "countries": jurisdiction_service.list_countries(),
            "currencies": jurisdiction_service.list_currencies(),
            "timezones": platform_service.COMMON_TIMEZONES,
            "effective_jurisdiction": effective_jurisdiction,
            "platform_defaults": platform_defaults,
            "tenant_country_raw": tenant_country_raw,
            "tenant_currency_raw": tenant_currency_raw,
            "tenant_timezone_raw": tenant_timezone_raw,
            "plan_features": plan_features,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the subscriptions & revenue management page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    _require_super_admin()

    return templates.TemplateResponse(
        "super_admin/subscriptions.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit_log_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Super admin page: filterable audit log viewer + config."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_super_admin()
    return templates.TemplateResponse(
        "super_admin/audit.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/activity", response_class=HTMLResponse)
async def live_activity_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Super admin page: live view of who is online and recent events."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_super_admin()
    return templates.TemplateResponse(
        "super_admin/activity.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/platform-banking", response_class=HTMLResponse)
async def platform_banking_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Super admin page to configure the platform's banking details
    (for tenants paying by EFT)."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    _require_super_admin()

    return templates.TemplateResponse(
        "super_admin/platform_banking.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/eft-payments", response_class=HTMLResponse)
async def eft_payments_queue(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Super admin page with the pending EFT payments queue."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    _require_super_admin()

    return templates.TemplateResponse(
        "super_admin/eft_payments.html",
        {
            "request": request,
            "user": user,
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


@router.get("/push-settings", response_class=HTMLResponse)
async def push_settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Super admin page: generate / rotate VAPID keypair, view subscription stats."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_super_admin()

    return templates.TemplateResponse(
        "super_admin/push_settings.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/payment-gateways", response_class=HTMLResponse)
async def payment_gateways_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Super admin page: configure the payment gateway for this instance."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_super_admin()

    return templates.TemplateResponse(
        "super_admin/payment_gateways.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/whatsapp-settings", response_class=HTMLResponse)
async def whatsapp_settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Super admin page: configure the WhatsApp bot for this instance +
    watch inbound messages arrive live."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_super_admin()

    return templates.TemplateResponse(
        "super_admin/whatsapp_settings.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/ai-settings", response_class=HTMLResponse)
async def ai_settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Super admin page: Anthropic API key + model for the AI-mode bot."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_super_admin()

    return templates.TemplateResponse(
        "super_admin/ai_settings.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/platform-settings", response_class=HTMLResponse)
async def platform_settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Super admin page: platform-wide defaults inherited by new tenants."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_super_admin()

    return templates.TemplateResponse(
        "super_admin/platform_settings.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )
