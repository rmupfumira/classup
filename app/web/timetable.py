"""Timetable web routes for HTML pages."""

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models import ClassSubject, ParentStudent, TeacherClass, Tenant
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.student_service import get_student_service
from app.services.timetable_service import get_timetable_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id,
    get_current_user_id_or_none,
    get_tenant_id,
)

router = APIRouter(prefix="/timetable")


async def _get_current_user(db: AsyncSession) -> User | None:
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    auth_service = get_auth_service()
    try:
        return await auth_service.get_current_user(db, user_id)
    except Exception:
        return None


def _require_auth(request: Request):
    user_id = get_current_user_id_or_none()
    if not user_id:
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie("access_token")
        return response
    return None


async def _ensure_feature_enabled(db: AsyncSession) -> None:
    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)
    features = (tenant.settings or {}).get("features", {}) if tenant else {}
    if not features.get("timetable_management"):
        raise ForbiddenException("Timetable management is not enabled for this school")


@router.get("", response_class=HTMLResponse)
async def timetable_home(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Admin list / role-based redirect."""
    redirect = _require_auth(request)
    if redirect:
        return redirect
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    await _ensure_feature_enabled(db)

    # Role-based routing
    if user.role == Role.TEACHER.value:
        return RedirectResponse(url="/timetable/my-schedule", status_code=302)

    if user.role == Role.PARENT.value:
        student_service = get_student_service()
        children = await student_service.get_my_children(db, user.id)
        if len(children) >= 1:
            return RedirectResponse(
                url=f"/timetable/child/{children[0].id}", status_code=302
            )
        return RedirectResponse(url="/dashboard", status_code=302)

    # Admin list view
    service = get_timetable_service()
    timetables, total = await service.list_timetables(db, page=1, page_size=50)

    context = {
        "request": request,
        "user": user,
        "timetables": timetables,
        "total": total,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("timetable/list.html", context)


@router.get("/config", response_class=HTMLResponse)
async def timetable_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Admin: edit the school-day config."""
    redirect = _require_auth(request)
    if redirect:
        return redirect
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    await _ensure_feature_enabled(db)

    if user.role not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    service = get_timetable_service()
    config = await service.get_or_create_config(db)
    await db.commit()

    context = {
        "request": request,
        "user": user,
        "config": config,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("timetable/config.html", context)


@router.get("/new", response_class=HTMLResponse)
async def timetable_new(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Admin: create form."""
    redirect = _require_auth(request)
    if redirect:
        return redirect
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    await _ensure_feature_enabled(db)

    if user.role not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    class_service = get_class_service()
    classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    context = {
        "request": request,
        "user": user,
        "classes": classes,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("timetable/new.html", context)


@router.get("/my-schedule", response_class=HTMLResponse)
async def my_schedule(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Teacher's own read-only schedule view."""
    redirect = _require_auth(request)
    if redirect:
        return redirect
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    await _ensure_feature_enabled(db)

    service = get_timetable_service()
    config = await service.get_or_create_config(db)
    entries = await service.get_teacher_timetable(db, user.id)
    await db.commit()

    # Build a {(day, period_index): entry} map for template rendering
    grid = {}
    for e in entries:
        class_name = (
            e.timetable.school_class.name
            if e.timetable and e.timetable.school_class
            else ""
        )
        grid[(e.day, e.period_index)] = {
            "subject_name": e.subject.name if e.subject else "",
            "class_name": class_name,
        }

    context = {
        "request": request,
        "user": user,
        "config": config,
        "grid": grid,
        "page_title": "My Timetable",
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("timetable/my_schedule.html", context)


@router.get("/child/{student_id}", response_class=HTMLResponse)
async def child_schedule(
    request: Request,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Parent view: child's class timetable."""
    redirect = _require_auth(request)
    if redirect:
        return redirect
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    await _ensure_feature_enabled(db)

    # Parent access check
    if user.role == Role.PARENT.value:
        check = await db.execute(
            select(ParentStudent).where(
                ParentStudent.parent_id == user.id,
                ParentStudent.student_id == student_id,
            )
        )
        if not check.scalar_one_or_none():
            raise ForbiddenException("You can only view your own children")

    student_service = get_student_service()
    student = await student_service.get_student(db, student_id)

    service = get_timetable_service()
    config = await service.get_or_create_config(db)
    timetable = await service.get_student_timetable(db, student_id)
    await db.commit()

    grid = {}
    if timetable:
        for e in timetable.entries:
            grid[(e.day, e.period_index)] = {
                "subject_name": e.subject.name if e.subject else "",
                "teacher_name": (
                    f"{e.teacher.first_name} {e.teacher.last_name}"
                    if e.teacher
                    else ""
                ),
            }

    # For parents with multiple children, pass the full list for switcher
    children = []
    if user.role == Role.PARENT.value:
        children = await student_service.get_my_children(db, user.id)

    context = {
        "request": request,
        "user": user,
        "student": student,
        "timetable": timetable,
        "config": config,
        "grid": grid,
        "children": children,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("timetable/child.html", context)


@router.get("/{timetable_id}", response_class=HTMLResponse)
async def timetable_edit(
    request: Request,
    timetable_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Admin editable grid view."""
    redirect = _require_auth(request)
    if redirect:
        return redirect
    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    await _ensure_feature_enabled(db)

    if user.role not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value, Role.TEACHER.value):
        raise ForbiddenException("Access denied")

    service = get_timetable_service()
    config = await service.get_or_create_config(db)
    timetable = await service.get_timetable(db, timetable_id)
    conflicts = await service.detect_conflicts(db, timetable_id)
    await db.commit()

    # Build grid map
    grid = {}
    for e in timetable.entries:
        grid[(e.day, e.period_index)] = {
            "entry_id": str(e.id),
            "subject_id": str(e.subject_id),
            "subject_name": e.subject.name if e.subject else "",
            "teacher_id": str(e.teacher_id) if e.teacher_id else "",
            "teacher_name": (
                f"{e.teacher.first_name} {e.teacher.last_name}"
                if e.teacher
                else ""
            ),
            "has_conflict": (e.day, e.period_index) in conflicts,
        }

    # Load class subjects for the dropdown
    class_subjects_q = await db.execute(
        select(ClassSubject)
        .where(ClassSubject.class_id == timetable.class_id)
        .order_by(ClassSubject.display_order)
    )
    class_subjects = list(class_subjects_q.scalars().all())

    # Load class teachers for the dropdown
    teacher_classes_q = await db.execute(
        select(TeacherClass).where(TeacherClass.class_id == timetable.class_id)
    )
    teacher_classes = list(teacher_classes_q.scalars().all())

    context = {
        "request": request,
        "user": user,
        "timetable": timetable,
        "config": config,
        "grid": grid,
        "class_subjects": class_subjects,
        "teacher_classes": teacher_classes,
        "is_admin": user.role in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value),
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("timetable/edit.html", context)
