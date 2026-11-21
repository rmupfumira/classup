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
from app.web._parent_context import (
    get_parent_child_context,
    remember_child,
    require_login,
)

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
    """Check authentication; redirect to login keeping ``?next=``."""
    return require_login(request)


async def _period_range(db: AsyncSession, period: str, today: date) -> tuple[date, date, str]:
    """(date_from, date_to, label) for a preset period (P-ATT-02).

    "term" uses the school calendar's current (or most recent) term when
    the jurisdiction calendar has one, else the calendar quarter.
    """
    from datetime import timedelta
    if period == "week":
        return today - timedelta(days=today.weekday()), today, "This week"
    if period == "year":
        return date(today.year, 1, 1), today, f"This year ({today.year})"
    if period == "30d":
        return today - timedelta(days=30), today, "Last 30 days"
    # term (default)
    try:
        from app.models import Tenant
        from app.services import calendar_service
        from app.utils.tenant_context import get_tenant_id
        tenant = await db.get(Tenant, get_tenant_id())
        country = str(((tenant.settings if tenant else None) or {}).get("country") or "ZA").upper()
        cal = await calendar_service.get_calendar(db, country, today.year)
        if cal is not None and cal.terms:
            current = cal.term_for(today)
            if current is None:
                past = [t for t in cal.terms if t.start <= today]
                current = past[-1] if past else None
            if current is not None:
                return current.start, min(current.end, today), f"{current.name} (this term)"
    except Exception:
        pass
    q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    return q_start, today, "This term"


@router.get("/my-child")
async def my_child_attendance(request: Request, db: AsyncSession = Depends(get_db)):
    """Parent entry point (tab bar / sidebar): the selected child's history.

    The child comes from the validated selected_child_id cookie; staff are
    sent to the normal attendance page.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect
    ctx = await get_parent_child_context(request, db)
    child = ctx.get("selected_child")
    if child is None:
        return RedirectResponse(url="/attendance" if not ctx else "/dashboard", status_code=302)
    return RedirectResponse(url=f"/attendance/student/{child.id}", status_code=302)


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

    # Attendance is taken by the class teacher, so a teacher only gets
    # classes they're class teacher of; subject-only classes get a friendly
    # state instead of a 404.
    not_class_teacher = False
    if user.role == Role.TEACHER.value:
        general_ids = class_ctx.get("general_class_ids") or set()
        subject_only = [c for c in classes if c.id not in general_ids]
        classes = [c for c in classes if c.id in general_ids]
        if class_id and class_id not in general_ids:
            not_class_teacher = True
        elif not class_id and not classes and subject_only:
            not_class_teacher = True
        if not_class_teacher:
            class_id = None

    # If no class selected, default to selected class from cookie (for teachers) or first class
    if not class_id and not not_class_teacher:
        selected = class_ctx.get("selected_class")
        if selected and any(c.id == selected.id for c in classes):
            class_id = selected.id
        elif classes:
            class_id = classes[0].id

    # Get attendance data for the selected class and date
    attendance_data = None
    if class_id:
        attendance_data = await attendance_service.get_class_attendance_for_date(
            db, class_id, target_date
        )

    # Weekend attendance is driven by school_type — boarding / weekly-
    # boarding / mixed schools can mark Saturdays (and Sundays for full
    # boarders). Pure DAY schools block weekends. Previously the client
    # JS blocked weekends unconditionally regardless of this setting,
    # which was a hard bug for any boarding school. 2026-11-18 tuning.
    from app.models import Tenant
    from app.utils.tenant_context import get_tenant_id
    tenant = await db.get(Tenant, get_tenant_id())
    school_type = (
        (tenant.settings if tenant else {}).get("school_type", "DAY")
        if tenant else "DAY"
    )
    weekends_allowed = school_type in ("BOARDING", "WEEKLY_BOARDING", "MIXED")

    context = {
        "request": request,
        "user": user,
        "classes": classes,
        "current_class_id": class_id,
        "target_date": target_date,
        "today": date.today(),
        "attendance_data": attendance_data,
        "not_class_teacher": not_class_teacher,
        "weekends_allowed": weekends_allowed,
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

    # Get classes for filter dropdown (teachers: classes they're class teacher of)
    if user.role == Role.TEACHER.value:
        general_ids = (await get_teacher_class_context(request, db)).get("general_class_ids") or set()
        classes = [c for c in await class_service.get_my_classes(db) if c.id in general_ids]
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

    # Period presets (P-ATT-02). Parents default to "This term"; staff
    # keep the previous default (last 30 days) unless they pick one.
    is_parent = user.role == Role.PARENT.value
    period = request.query_params.get("period")
    if period not in ("week", "term", "year", "30d", "custom"):
        period = None
    if date_from or date_to:
        period = "custom"
    elif period is None and is_parent:
        period = "term"
    period_label = None
    if period and period != "custom":
        date_from, date_to, period_label = await _period_range(db, period, date.today())
    elif period == "custom":
        period_label = "Custom dates"

    # Parents get every day in the period on one page so the
    # "Needs a look" list (exceptions first) is complete.
    page_size = 400 if is_parent else 30
    records, total, summary = await attendance_service.get_student_attendance_history(
        db,
        student_id=student_id,
        date_from=date_from,
        date_to=date_to,
        page=1 if is_parent else page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size
    exceptions = [r for r in records if r.status != "PRESENT"] if is_parent else []

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
        "period": period,
        "period_label": period_label,
        "exceptions": exceptions,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    if is_parent:
        child_ctx = await get_parent_child_context(request, db, explicit=student_id)
        context.update(child_ctx)
        response = templates.TemplateResponse("attendance/student_history.html", context)
        remember_child(response, child_ctx)
        return response
    return templates.TemplateResponse("attendance/student_history.html", context)
