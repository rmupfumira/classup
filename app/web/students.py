"""Student web routes for HTML pages."""

import csv
import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException, NotFoundException
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.invitation_service import get_invitation_service
from app.services.student_service import get_student_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id,
    get_current_user_id_or_none,
    get_current_user_role,
)
from app.web.helpers import get_teacher_class_context

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
    grade_level_id: str | None = None,
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

    # Sanitize grade_level_id: empty string → None, valid UUID string → UUID
    parsed_grade_level_id = None
    if grade_level_id and grade_level_id.strip():
        try:
            parsed_grade_level_id = uuid.UUID(grade_level_id)
        except ValueError:
            pass

    student_service = get_student_service()
    class_service = get_class_service()

    # For teachers, default to selected class when no class_id provided
    teacher_ctx = {}
    if user.role == Role.TEACHER.value:
        teacher_ctx = await get_teacher_class_context(request, db)
        if class_id is None and teacher_ctx.get("selected_class_id"):
            class_id = teacher_ctx["selected_class_id"]

    # Parents see their own children
    if user.role == Role.PARENT.value:
        students = await student_service.get_my_children(db, user.id)
        total = len(students)
        classes = []
    else:
        students, total = await student_service.get_students(
            db,
            class_id=class_id,
            grade_level_id=parsed_grade_level_id,
            search=search,
            page=page,
            page_size=20,
        )
        # Get classes for filter dropdown
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    total_pages = (total + 19) // 20

    # Build export query string from current filters
    export_parts = []
    if class_id:
        export_parts.append(f"class_id={class_id}")
    if grade_level_id and grade_level_id.strip():
        export_parts.append(f"grade_level_id={grade_level_id}")
    if search:
        export_parts.append(f"search={search}")
    export_query = "&".join(export_parts)

    context = {
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
        "export_query": export_query,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    # Inject teacher class context for navbar class selector
    if teacher_ctx:
        context.update(teacher_ctx)
    return templates.TemplateResponse("students/list.html", context)


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

    context = {
        "request": request,
        "user": user,
        "classes": classes,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("students/create.html", context)


@router.get("/export/csv")
async def students_export_csv(
    request: Request,
    class_id: uuid.UUID | None = None,
    grade_level_id: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Export student list to CSV."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    if not permissions.can_manage_students():
        raise ForbiddenException("You don't have permission to export students")

    parsed_grade_level_id = None
    if grade_level_id and grade_level_id.strip():
        try:
            parsed_grade_level_id = uuid.UUID(grade_level_id)
        except ValueError:
            pass

    student_service = get_student_service()
    students, _ = await student_service.get_students(
        db, class_id=class_id, grade_level_id=parsed_grade_level_id,
        search=search, page=1, page_size=5000,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["First Name", "Last Name", "Class", "Grade Level", "Date of Birth", "Gender", "Status", "Enrollment Date"])
    for s in students:
        writer.writerow([
            s.first_name,
            s.last_name,
            s.school_class.name if s.school_class else "",
            (s.school_class.grade_level_rel.name if s.school_class and hasattr(s.school_class, 'grade_level_rel') and s.school_class.grade_level_rel else s.age_group or ""),
            str(s.date_of_birth) if s.date_of_birth else "",
            s.gender or "",
            "Active" if s.is_active else "Inactive",
            str(s.enrollment_date) if s.enrollment_date else "",
        ])

    output.seek(0)
    today = date.today().isoformat()
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=students_{today}.csv"},
    )


@router.get("/export/pdf")
async def students_export_pdf(
    request: Request,
    class_id: uuid.UUID | None = None,
    grade_level_id: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Export student list to PDF."""
    from fpdf import FPDF

    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    if not permissions.can_manage_students():
        raise ForbiddenException("You don't have permission to export students")

    parsed_grade_level_id = None
    if grade_level_id and grade_level_id.strip():
        try:
            parsed_grade_level_id = uuid.UUID(grade_level_id)
        except ValueError:
            pass

    student_service = get_student_service()
    students, total = await student_service.get_students(
        db, class_id=class_id, grade_level_id=parsed_grade_level_id,
        search=search, page=1, page_size=5000,
    )

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Student List", ln=True, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Generated: {date.today().strftime('%d %B %Y')}  |  Total: {total} students", ln=True, align="C")
    pdf.ln(4)

    # Table header
    col_widths = [50, 50, 50, 45, 30, 25, 27]
    headers = ["First Name", "Last Name", "Class", "Grade Level", "DOB", "Gender", "Status"]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(240, 240, 240)
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 8, h, border=1, fill=True)
    pdf.ln()

    # Table rows
    pdf.set_font("Helvetica", "", 8)
    for s in students:
        grade = ""
        if s.school_class and hasattr(s.school_class, 'grade_level_rel') and s.school_class.grade_level_rel:
            grade = s.school_class.grade_level_rel.name
        elif s.age_group:
            grade = s.age_group

        row = [
            s.first_name,
            s.last_name,
            s.school_class.name if s.school_class else "",
            grade,
            str(s.date_of_birth) if s.date_of_birth else "",
            s.gender or "",
            "Active" if s.is_active else "Inactive",
        ]
        for i, val in enumerate(row):
            pdf.cell(col_widths[i], 7, str(val)[:30], border=1)
        pdf.ln()

    pdf_bytes = pdf.output()
    today = date.today().isoformat()
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=students_{today}.pdf"},
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

    # Fetch pending invitations for this student (for staff)
    pending_invitations = []
    if permissions.can_invite_parents():
        invitation_service = get_invitation_service()
        invitations, _ = await invitation_service.list_invitations(
            db, status="PENDING", student_id=student_id, page_size=50,
        )
        pending_invitations = invitations

    context = {
        "request": request,
        "user": user,
        "student": student,
        "pending_invitations": pending_invitations,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("students/detail.html", context)


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

    context = {
        "request": request,
        "user": user,
        "student": student,
        "classes": classes,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("students/edit.html", context)
