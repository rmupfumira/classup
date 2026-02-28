"""Teacher web routes for HTML pages."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.teacher_invitation_service import get_teacher_invitation_service
from app.services.user_service import get_user_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)

router = APIRouter(prefix="/teachers")


async def _get_current_user(db: AsyncSession) -> User | None:
    """Get the current user from the database."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    auth_service = get_auth_service()
    try:
        return await auth_service.get_current_user(db, user_id)
    except Exception:
        return None


def _require_auth(request: Request):
    """Check authentication and return redirect if not authenticated."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie("access_token")
        return response
    return None


@router.get("", response_class=HTMLResponse)
async def teachers_list(
    request: Request,
    search: str | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the teachers list page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_manage_classes():
        raise ForbiddenException("You don't have permission to manage teachers")

    user_service = get_user_service()
    teachers, total = await user_service.get_teachers_paginated(
        db, search=search, page=page, page_size=20,
    )

    total_pages = (total + 19) // 20

    # Get pending invitations
    invitation_service = get_teacher_invitation_service()
    pending_invitations, _ = await invitation_service.list_invitations(
        db, status="PENDING",
    )

    # Also get expired invitations so admin sees all non-accepted ones
    expired_invitations, _ = await invitation_service.list_invitations(
        db, status="EXPIRED",
    )

    all_invitations = list(pending_invitations) + list(expired_invitations)

    return templates.TemplateResponse(
        "teachers/list.html",
        {
            "request": request,
            "user": user,
            "teachers": teachers,
            "pending_invitations": all_invitations,
            "search": search,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/{teacher_id}/edit", response_class=HTMLResponse)
async def teacher_edit(
    request: Request,
    teacher_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the teacher edit page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_manage_classes():
        raise ForbiddenException("You don't have permission to manage teachers")

    user_service = get_user_service()
    try:
        teacher = await user_service.get_user(db, teacher_id)
    except Exception:
        return RedirectResponse(url="/teachers", status_code=302)

    from app.models.user import Role
    if teacher.role != Role.TEACHER.value:
        return RedirectResponse(url="/teachers", status_code=302)

    return templates.TemplateResponse(
        "teachers/edit.html",
        {
            "request": request,
            "user": user,
            "teacher": teacher,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )
