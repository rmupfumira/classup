"""Student web routes for HTML pages."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException, NotFoundException
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.student_service import get_student_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id,
    get_current_user_id_or_none,
    get_current_user_role,
)

router = APIRouter(prefix="/students")


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
async def students_list(
    request: Request,
    class_id: uuid.UUID | None = None,
    grade_level_id: uuid.UUID | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the students list page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_manage_students() and user.role != Role.PARENT.value:
        raise ForbiddenException("You don't have permission to view students")

    student_service = get_student_service()
    class_service = get_class_service()

    # Parents see their own children
    if user.role == Role.PARENT.value:
        students = await student_service.get_my_children(db, user.id)
        total = len(students)
        classes = []
    else:
        students, total = await student_service.get_students(
            db,
            class_id=class_id,
            grade_level_id=grade_level_id,
            search=search,
            page=page,
            page_size=20,
        )
        # Get classes for filter dropdown
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    total_pages = (total + 19) // 20

    return templates.TemplateResponse(
        "students/list.html",
        {
            "request": request,
            "user": user,
            "students": students,
            "classes": classes,
            "current_class_id": class_id,
            "current_grade_level_id": str(grade_level_id) if grade_level_id else None,
            "search": search,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def student_create_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the student creation form."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_manage_students():
        raise ForbiddenException("You don't have permission to create students")

    class_service = get_class_service()
    classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    return templates.TemplateResponse(
        "students/create.html",
        {
            "request": request,
            "user": user,
            "classes": classes,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/{student_id}", response_class=HTMLResponse)
async def student_detail(
    request: Request,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the student detail page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    student_service = get_student_service()
    student = await student_service.get_student(db, student_id)

    # Parents can only view their own children
    if user.role == Role.PARENT.value:
        parent_ids = [ps.parent_id for ps in student.parent_students]
        if user.id not in parent_ids:
            raise ForbiddenException("You can only view your own children")

    return templates.TemplateResponse(
        "students/detail.html",
        {
            "request": request,
            "user": user,
            "student": student,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/{student_id}/edit", response_class=HTMLResponse)
async def student_edit_form(
    request: Request,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the student edit form."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_manage_students():
        raise ForbiddenException("You don't have permission to edit students")

    student_service = get_student_service()
    class_service = get_class_service()

    student = await student_service.get_student(db, student_id)
    classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    return templates.TemplateResponse(
        "students/edit.html",
        {
            "request": request,
            "user": user,
            "student": student,
            "classes": classes,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )
