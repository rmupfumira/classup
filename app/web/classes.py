"""Class web routes for HTML pages."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.user_service import get_user_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)
from app.web.helpers import get_teacher_class_context

router = APIRouter(prefix="/classes")


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
async def classes_list(
    request: Request,
    search: str | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the classes list page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.is_staff:
        raise ForbiddenException("You don't have permission to view classes")

    class_service = get_class_service()
    classes, total = await class_service.get_classes(
        db,
        search=search,
        page=page,
        page_size=20,
    )

    total_pages = (total + 19) // 20

    context = {
        "request": request,
        "user": user,
        "classes": classes,
        "search": search,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("classes/list.html", context)


@router.get("/new", response_class=HTMLResponse)
async def class_create_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the class creation form."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_manage_classes():
        raise ForbiddenException("You don't have permission to create classes")

    context = {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("classes/create.html", context)


@router.get("/{class_id}", response_class=HTMLResponse)
async def class_detail(
    request: Request,
    class_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the class detail page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.is_staff:
        raise ForbiddenException("You don't have permission to view classes")

    class_service = get_class_service()
    school_class = await class_service.get_class(db, class_id)
    students = await class_service.get_class_students(db, class_id)
    teachers_data = await class_service.get_class_teachers(db, class_id)

    context = {
        "request": request,
        "user": user,
        "school_class": school_class,
        "students": students,
        "teachers": [(u, tc) for u, tc in teachers_data],
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("classes/detail.html", context)


@router.get("/{class_id}/edit", response_class=HTMLResponse)
async def class_edit_form(
    request: Request,
    class_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the class edit form."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_manage_classes():
        raise ForbiddenException("You don't have permission to edit classes")

    class_service = get_class_service()
    school_class = await class_service.get_class(db, class_id)

    context = {
        "request": request,
        "user": user,
        "school_class": school_class,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("classes/edit.html", context)


@router.get("/{class_id}/subjects")
async def class_subjects_redirect(
    class_id: uuid.UUID,
):
    """Redirect to the class subjects management page."""
    return RedirectResponse(
        url=f"/settings/academic/classes/{class_id}/subjects",
        status_code=302,
    )


@router.get("/{class_id}/manage-teachers", response_class=HTMLResponse)
async def manage_teachers(
    request: Request,
    class_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the manage teachers page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_manage_classes():
        raise ForbiddenException("You don't have permission to manage teachers")

    class_service = get_class_service()
    user_service = get_user_service()

    school_class = await class_service.get_class(db, class_id)
    teachers_data = await class_service.get_class_teachers(db, class_id)

    # Get all available teachers
    all_teachers = await user_service.get_users_by_role(db, Role.TEACHER)

    # Filter out already assigned teachers
    assigned_teacher_ids = {u.id for u, _ in teachers_data}
    available_teachers = [t for t in all_teachers if t.id not in assigned_teacher_ids]

    context = {
        "request": request,
        "user": user,
        "school_class": school_class,
        "assigned_teachers": [(u, tc) for u, tc in teachers_data],
        "available_teachers": available_teachers,
        "total_teachers": len(all_teachers),
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("classes/manage_teachers.html", context)
