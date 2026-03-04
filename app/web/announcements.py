"""Announcement web routes for HTML pages."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.services.announcement_service import get_announcement_service
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
)
from app.web.helpers import get_teacher_class_context

router = APIRouter(prefix="/announcements")


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


@router.get("", response_class=HTMLResponse)
async def announcements_list(
    request: Request,
    level: str | None = None,
    severity: str | None = None,
    class_id: uuid.UUID | None = None,
    active_only: bool = False,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the announcements list page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    permissions = PermissionChecker(role)

    service = get_announcement_service()
    announcements, total = await service.get_announcements(
        db,
        level=level,
        severity=severity,
        class_id=class_id,
        active_only=active_only,
        page=page,
        page_size=20,
    )

    total_pages = (total + 20 - 1) // 20

    # Get classes for filter dropdown
    classes = []
    class_service = get_class_service()
    try:
        if role == "TEACHER":
            classes = await class_service.get_my_classes(db)
        elif role in ("SCHOOL_ADMIN", "SUPER_ADMIN"):
            classes_result, _ = await class_service.get_classes(db, page=1, page_size=100)
            classes = list(classes_result)
    except Exception:
        pass

    # Teacher class context for nav
    class_ctx = {}
    if role == "TEACHER":
        class_ctx = await get_teacher_class_context(request, db)

    context = {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "announcements": announcements,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "classes": classes,
        "permissions": permissions,
        # Current filter values
        "filter_level": level,
        "filter_severity": severity,
        "filter_class_id": str(class_id) if class_id else None,
        "filter_active_only": active_only,
    }
    context.update(class_ctx)

    return templates.TemplateResponse("announcements/list.html", context)


@router.get("/create", response_class=HTMLResponse)
async def announcements_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the create announcement form."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    permissions = PermissionChecker(role)

    if not permissions.can_send_class_announcements():
        return RedirectResponse(url="/announcements", status_code=302)

    # Get classes for dropdown
    classes = []
    class_service = get_class_service()
    try:
        if role == "TEACHER":
            classes = await class_service.get_my_classes(db)
        elif role in ("SCHOOL_ADMIN", "SUPER_ADMIN"):
            classes_result, _ = await class_service.get_classes(db, page=1, page_size=100)
            classes = list(classes_result)
    except Exception:
        pass

    # Teacher class context for nav
    class_ctx = {}
    if role == "TEACHER":
        class_ctx = await get_teacher_class_context(request, db)

    context = {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "classes": classes,
        "permissions": permissions,
    }
    context.update(class_ctx)

    return templates.TemplateResponse("announcements/create.html", context)


@router.get("/{announcement_id}", response_class=HTMLResponse)
async def announcement_detail(
    request: Request,
    announcement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the announcement detail page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    permissions = PermissionChecker(role)

    service = get_announcement_service()
    try:
        announcement = await service.get_announcement(db, announcement_id)
    except Exception:
        return RedirectResponse(url="/announcements", status_code=302)

    # Teacher class context for nav
    class_ctx = {}
    if role == "TEACHER":
        class_ctx = await get_teacher_class_context(request, db)

    context = {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "announcement": announcement,
        "permissions": permissions,
    }
    context.update(class_ctx)

    return templates.TemplateResponse("announcements/detail.html", context)
