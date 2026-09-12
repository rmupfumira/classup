"""Parent management web routes (school admin).

Distinct from the parent-side pages: these are the admin's view of
parent accounts on their tenant — the analog of the teachers pages.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models.user import User
from app.services.auth_service import get_auth_service
from app.services.user_service import get_user_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)

router = APIRouter(prefix="/parents")


async def _get_current_user(db: AsyncSession) -> User | None:
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    try:
        return await get_auth_service().get_current_user(db, user_id)
    except Exception:
        return None


def _require_auth(request: Request):
    """Login redirect that preserves the intended URL as ?next=.

    Consistent with the recent billing fix so deep links (email/
    WhatsApp links to a parent detail page, for instance) survive
    a login round-trip.
    """
    if not get_current_user_id_or_none():
        from urllib.parse import quote
        next_url = request.url.path
        if request.url.query:
            next_url += "?" + request.url.query
        response = RedirectResponse(
            url=f"/login?next={quote(next_url, safe='/?&=')}",
            status_code=302,
        )
        response.delete_cookie("access_token")
        return response
    return None


@router.get("", response_class=HTMLResponse)
async def parents_list(
    request: Request,
    search: str | None = None,
    include_inactive: bool = False,
    opted_in_only: bool = False,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the parents management list."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    # Admins only — teachers see students-with-parents on the student
    # page, but bulk parent management is an admin concern.
    if not permissions.is_school_admin and not permissions.is_super_admin:
        raise ForbiddenException("You don't have permission to manage parents")

    page_size = 25
    service = get_user_service()
    parents, total = await service.list_parents(
        db,
        search=search,
        include_inactive=include_inactive,
        opted_in_only=opted_in_only,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    return templates.TemplateResponse(
        "parents/list.html",
        {
            "request": request,
            "user": user,
            "parents": parents,
            "search": search or "",
            "include_inactive": include_inactive,
            "opted_in_only": opted_in_only,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/{parent_id}", response_class=HTMLResponse)
async def parent_detail(
    request: Request,
    parent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the single-parent management page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    if not permissions.is_school_admin and not permissions.is_super_admin:
        raise ForbiddenException("You don't have permission to manage parents")

    service = get_user_service()
    parent = await service.get_parent(db, parent_id)

    return templates.TemplateResponse(
        "parents/detail.html",
        {
            "request": request,
            "user": user,
            "parent": parent,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )
