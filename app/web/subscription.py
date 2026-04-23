"""Tenant-facing subscription management web routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth_service import get_auth_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
)

router = APIRouter(tags=["subscription"])


@router.get("/subscription", response_class=HTMLResponse)
async def subscription_page(
    request: Request,
    locked: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Render the subscription management page for school admins.

    If ?locked=<feature> is present (e.g. redirected from a gated page),
    shows a banner explaining which feature needs an upgrade.
    """
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    auth_service = get_auth_service()
    try:
        user = await auth_service.get_current_user(db, user_id)
    except Exception:
        return RedirectResponse(url="/login", status_code=302)

    # Resolve friendly label for the locked feature (if any)
    locked_label: str | None = None
    if locked:
        from app.utils.permissions import FEATURE_LABELS
        locked_label = FEATURE_LABELS.get(locked, locked.replace("_", " ").title())

    return templates.TemplateResponse(
        "subscription.html",
        {
            "request": request,
            "user": user,
            "locked_feature": locked,
            "locked_feature_label": locked_label,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(get_current_user_role()),
        },
    )


@router.get("/subscription/eft", response_class=HTMLResponse)
async def eft_payment_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Tenant-facing EFT payment upload page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    auth_service = get_auth_service()
    try:
        user = await auth_service.get_current_user(db, user_id)
    except Exception:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "subscription_eft.html",
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(get_current_user_role()),
        },
    )
