"""Attendance web routes for HTML pages."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models.user import Role, User
from app.services.attendance_service import get_attendance_service
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.student_service import get_student_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)
from app.web.helpers import get_teacher_class_context

router = APIRouter(prefix="/attendance")


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
async def attendance_daily(
    request: Request,
    class_id: uuid.UUID | None = None,
    target_date: date | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Render the daily attendance page.

    This is the main attendance-taking view for teachers.
    Shows all students in a class with toggle buttons for each status.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_record_attendance():
        raise ForbiddenException("You don't have permission to record attendance")

    class_service = get_class_service()
    attendance_service = get_attendance_service()

    # Get teacher's classes
    if user.role == Role.TEACHER.value:
        classes = await class_service.get_my_classes(db)
    else:
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    # Default to today's date
    if not target_date:
        target_date = date.today()

    # Get teacher class context for navbar
    class_ctx = {}
    if user.role == Role.TEACHER.value:
        class_ctx = await get_teacher_class_context(request, db)

    # If no class selected, default to selected class from cookie (for teachers) or first class
    if not class_id:
        if class_ctx.get("selected_class"):
            class_id = class_ctx["selected_class"].id
        elif classes:
            class_id = classes[0].id

    # Get attendance data for the selected class and date
    attendance_data = None
    if class_id:
        attendance_data = await attendance_service.get_class_attendance_for_date(
            db, class_id, target_date
        )

    context = {
        "request": request,
        "user": user,
        "classes": classes,
        "current_class_id": class_id,
        "target_date": target_date,
        "attendance_data": attendance_data,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    context.update(class_ctx)
    return templates.TemplateResponse("attendance/daily.html", context)


@router.get("/history", response_class=HTMLResponse)
async def attendance_history(
    request: Request,
    class_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the attendance history page.

    Shows historical attendance records with filtering options.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_view_attendance():
        raise ForbiddenException("You don't have permission to view attendance")

    class_service = get_class_service()
    attendance_service = get_attendance_service()

    # Get classes for filter dropdown
    if user.role == Role.TEACHER.value:
        classes = await class_service.get_my_classes(db)
    else:
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    # Get attendance records
    records, total = await attendance_service.get_attendance_records(
        db,
        class_id=class_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=20,
    )

    total_pages = (total + 19) // 20

    # Get stats for the period
    stats = await attendance_service.get_attendance_stats(
        db,
        class_id=class_id,
        date_from=date_from,
        date_to=date_to,
    )

    context = {
        "request": request,
        "user": user,
        "classes": classes,
        "records": records,
        "stats": stats,
        "current_class_id": class_id,
        "date_from": date_from,
        "date_to": date_to,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("attendance/history.html", context)


@router.get("/student/{student_id}", response_class=HTMLResponse)
async def student_attendance_history(
    request: Request,
    student_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render attendance history for a specific student.

    Parents can view their own children's attendance.
    Teachers/admins can view any student in their scope.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    student_service = get_student_service()
    attendance_service = get_attendance_service()

    # Get the student
    student = await student_service.get_student(db, student_id)

    # Parents can only view their own children
    if user.role == Role.PARENT.value:
        parent_ids = [ps.parent_id for ps in student.parent_students]
        if user.id not in parent_ids:
            raise ForbiddenException("You can only view your own children's attendance")
    elif not permissions.can_view_attendance():
        raise ForbiddenException("You don't have permission to view attendance")

    # Get attendance history
    records, total, summary = await attendance_service.get_student_attendance_history(
        db,
        student_id=student_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=30,
    )

    total_pages = (total + 29) // 30

    context = {
        "request": request,
        "user": user,
        "student": student,
        "records": records,
        "summary": summary,
        "date_from": date_from,
        "date_to": date_to,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("attendance/student_history.html", context)
