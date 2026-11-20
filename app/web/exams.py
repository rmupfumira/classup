"""Exam module web routes — list, detail, marking."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models import SchoolClass, Student
from app.models.exam import Exam, ExamResult, ExamSubject
from app.models.report import ReportTemplate
from app.services.auth_service import get_auth_service
from app.services.exam_service import get_exam_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id,
)

router = APIRouter(prefix="/exams", tags=["Exams"])


def _require_auth(request: Request):
    user_id = get_current_user_id_or_none()
    if not user_id:
        from urllib.parse import quote
        next_url = request.url.path
        if request.url.query:
            next_url += "?" + request.url.query
        return RedirectResponse(
            url=f"/login?next={quote(next_url, safe='/?&=')}",
            status_code=302,
        )
    return None


async def _guard(request: Request, db: AsyncSession):
    """Shared auth + feature gate for every exam page.

    Returns either a RedirectResponse or a (user, tenant) tuple so the
    caller can build its template context directly.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect, None
    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN", "TEACHER"):
        return RedirectResponse(url="/dashboard", status_code=302), None
    user = await get_auth_service().get_current_user(db, get_current_user_id_or_none())
    from app.models import Tenant
    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id) if tenant_id else None
    features = (tenant.settings or {}).get("features", {}) if tenant else {}
    if not features.get("exam_management"):
        return RedirectResponse(
            url="/subscription?locked=exam_management", status_code=302,
        ), None
    return None, (user, tenant)


@router.get("", response_class=HTMLResponse)
async def exams_list(request: Request, db: AsyncSession = Depends(get_db)):
    redirect, ctx = await _guard(request, db)
    if redirect:
        return redirect
    user, tenant = ctx
    svc = get_exam_service()
    exams = await svc.list_exams(db)

    classes = (await db.execute(
        select(SchoolClass).where(
            SchoolClass.tenant_id == get_tenant_id(),
            SchoolClass.deleted_at.is_(None),
            SchoolClass.is_active.is_(True),
        ).order_by(SchoolClass.name)
    )).scalars().all()
    templates_list = (await db.execute(
        select(ReportTemplate).where(
            ReportTemplate.tenant_id == get_tenant_id(),
            ReportTemplate.report_type == "REPORT_CARD",
            ReportTemplate.is_active.is_(True),
            ReportTemplate.deleted_at.is_(None),
        )
    )).scalars().all()

    # Marking progress per exam: captured results vs papers x class size.
    tenant_id = get_tenant_id()
    marked_rows = (await db.execute(
        select(ExamSubject.exam_id, func.count(ExamResult.id))
        .join(ExamResult, ExamResult.exam_subject_id == ExamSubject.id)
        .where(
            ExamSubject.tenant_id == tenant_id,
            or_(ExamResult.marks_obtained.is_not(None), ExamResult.is_absent.is_(True)),
        )
        .group_by(ExamSubject.exam_id)
    )).all()
    marked_by_exam = {exam_id: n for exam_id, n in marked_rows}
    student_rows = (await db.execute(
        select(Student.class_id, func.count(Student.id))
        .where(
            Student.tenant_id == tenant_id,
            Student.deleted_at.is_(None),
            Student.is_active.is_(True),
        )
        .group_by(Student.class_id)
    )).all()
    students_by_class = {class_id: n for class_id, n in student_rows}
    progress = {}
    for e in exams:
        students = students_by_class.get(e.class_id, 0)
        expected = len(e.exam_subjects) * students
        marked = marked_by_exam.get(e.id, 0)
        progress[e.id] = {
            "students": students,
            "pct": min(100, round(marked * 100 / expected)) if expected else 0,
        }

    return templates.TemplateResponse(
        "exams/list.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "exams": exams,
            "progress": progress,
            "classes": classes,
            "templates_list": templates_list,
            "permissions": PermissionChecker(user.role),
            "current_language": get_current_language(),
        },
    )


@router.get("/{exam_id}", response_class=HTMLResponse)
async def exam_detail(
    request: Request,
    exam_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    redirect, ctx = await _guard(request, db)
    if redirect:
        return redirect
    user, tenant = ctx
    exam = await get_exam_service().get_exam(db, exam_id)

    return templates.TemplateResponse(
        "exams/detail.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "exam": exam,
            "permissions": PermissionChecker(user.role),
            "current_language": get_current_language(),
        },
    )


@router.get("/{exam_id}/papers/{exam_subject_id}/mark", response_class=HTMLResponse)
async def mark_paper(
    request: Request,
    exam_id: uuid.UUID,
    exam_subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Per-paper marking page — enter marks for every student in the class."""
    redirect, ctx = await _guard(request, db)
    if redirect:
        return redirect
    user, tenant = ctx
    exam = await get_exam_service().get_exam(db, exam_id)
    exam_subject = next(
        (es for es in exam.exam_subjects if es.id == exam_subject_id),
        None,
    )
    if exam_subject is None:
        raise ForbiddenException("Paper not in this exam.")

    students = (await db.execute(
        select(Student).where(
            Student.tenant_id == get_tenant_id(),
            Student.class_id == exam.class_id,
            Student.deleted_at.is_(None),
            Student.is_active.is_(True),
        ).order_by(Student.last_name, Student.first_name)
    )).scalars().all()

    return templates.TemplateResponse(
        "exams/mark.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "exam": exam,
            "exam_subject": exam_subject,
            "students": students,
            "permissions": PermissionChecker(user.role),
            "current_language": get_current_language(),
        },
    )
