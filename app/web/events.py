"""School events + parent RSVPs — HTML pages.

Two audiences share the same URL (``/events``) and template:
  - Staff (SCHOOL_ADMIN, TEACHER): sees the full list, can create /
    edit / cancel, sees RSVP counts.
  - Parents: sees only events they're invited to; can RSVP with a tap.

The template branches on ``user.role`` so we don't fork the URL space.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Tenant
from app.models.user import User
from app.services.auth_service import get_auth_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id,
)


router = APIRouter(prefix="/events")


async def _current_user(db: AsyncSession) -> User | None:
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    try:
        return await get_auth_service().get_current_user(db, user_id)
    except Exception:
        return None


@router.get("", response_class=HTMLResponse)
async def events_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List page. Same URL for staff + parent; template branches on
    user.role."""
    user = await _current_user(db)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id) if tenant_id else None

    # Class list — used by the "scope = CLASS" picker on the create form.
    # get_classes reads tenant_id from the request context internally.
    from app.services.class_service import get_class_service
    classes: list = []
    if user.role in ("SCHOOL_ADMIN", "TEACHER"):
        try:
            classes, _total = await get_class_service().get_classes(
                db, is_active=True, page=1, page_size=200,
            )
        except Exception:
            classes = []

    return templates.TemplateResponse(
        "events/index.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "classes": classes,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(get_current_user_role()),
        },
    )


@router.get("/{event_id}", response_class=HTMLResponse)
async def event_detail(
    event_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Single-event view. Parent RSVP happens via the API from here."""
    user = await _current_user(db)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id) if tenant_id else None

    return templates.TemplateResponse(
        "events/detail.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "event_id": event_id,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(get_current_user_role()),
        },
    )
