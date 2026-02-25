"""Reports web routes for HTML pages."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.report_service import get_report_service
from app.services.student_service import get_student_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)
from app.web.helpers import get_teacher_class_context

router = APIRouter(prefix="/reports")


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
async def reports_list(
    request: Request,
    class_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    report_date: date | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the reports list page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    report_service = get_report_service()
    class_service = get_class_service()

    # Get classes for filter dropdown
    if user.role == Role.TEACHER.value:
        classes = await class_service.get_my_classes(db)
    elif user.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)
    else:
        classes = []

    # Get reports
    reports, total = await report_service.get_reports(
        db,
        class_id=class_id,
        student_id=student_id,
        report_date=report_date,
        status=status,
        page=page,
        page_size=20,
    )

    total_pages = (total + 19) // 20

    context = {
        "request": request,
        "user": user,
        "reports": reports,
        "classes": classes,
        "current_class_id": class_id,
        "current_student_id": student_id,
        "current_date": report_date,
        "current_status": status,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("reports/list.html", context)


@router.get("/create", response_class=HTMLResponse)
async def reports_create(
    request: Request,
    student_id: uuid.UUID | None = None,
    template_id: uuid.UUID | None = None,
    report_date: date | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Render the report creation page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Only staff can create reports
    if user.role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value, Role.TEACHER.value):
        return RedirectResponse(url="/reports", status_code=302)

    permissions = PermissionChecker(user.role)
    report_service = get_report_service()
    class_service = get_class_service()
    student_service = get_student_service()

    # Get classes for dropdown
    if user.role == Role.TEACHER.value:
        classes = await class_service.get_my_classes(db)
    else:
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    # Get selected student if provided
    selected_student = None
    if student_id:
        selected_student = await student_service.get_student(db, student_id)

    # Get applicable templates for student
    templates_list = []
    if student_id:
        templates_list = await report_service.get_templates_for_student(db, student_id)

    # Get selected template if provided
    selected_template = None
    if template_id:
        selected_template = await report_service.get_template(db, template_id)

    context = {
        "request": request,
        "user": user,
        "classes": classes,
        "templates": templates_list,
        "selected_student": selected_student,
        "selected_template": selected_template,
        "report_date": report_date or date.today(),
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("reports/create.html", context)


@router.get("/{report_id}", response_class=HTMLResponse)
async def reports_view(
    request: Request,
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the report view page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    report_service = get_report_service()

    # Get the report
    report = await report_service.get_report(db, report_id)
    if not report:
        return RedirectResponse(url="/reports", status_code=302)

    # TODO: Check parent access (only own children's reports)

    # Get tenant for branding on report cards
    from app.models import Tenant
    from app.utils.tenant_context import get_tenant_id
    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    # Use report card template for REPORT_CARD type reports
    template_name = "reports/view.html"
    grading_system = None
    class_subjects = []

    if report.template and report.template.report_type == "REPORT_CARD":
        template_name = "reports/view_report_card.html"

        # Get grading system for report cards
        from app.services.academic_service import get_academic_service
        academic_service = get_academic_service()

        # Use template's grading system or default
        if report.template.grading_system_id:
            grading_system = await academic_service.get_grading_system(
                db, report.template.grading_system_id
            )
        if not grading_system:
            grading_system = await academic_service.get_default_grading_system(db)

        # Get class subjects for this class
        if report.class_id:
            class_subjects = await academic_service.get_class_subjects(db, report.class_id)

    context = {
        "request": request,
        "user": user,
        "report": report,
        "tenant": tenant,
        "grading_system": grading_system,
        "class_subjects": class_subjects,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse(template_name, context)


@router.get("/{report_id}/edit", response_class=HTMLResponse)
async def reports_edit(
    request: Request,
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the report edit page (only for draft reports)."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Only staff can edit reports
    if user.role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value, Role.TEACHER.value):
        return RedirectResponse(url="/reports", status_code=302)

    permissions = PermissionChecker(user.role)
    report_service = get_report_service()

    # Get the report
    report = await report_service.get_report(db, report_id)
    if not report:
        return RedirectResponse(url="/reports", status_code=302)

    # Check if report is still draft
    if report.is_finalized:
        return RedirectResponse(url=f"/reports/{report_id}", status_code=302)

    context = {
        "request": request,
        "user": user,
        "report": report,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("reports/edit.html", context)


# ============== Template Management Routes ==============


@router.get("/templates/manage", response_class=HTMLResponse)
async def templates_manage(
    request: Request,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the templates management page (admin only)."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Only admins can manage templates
    if user.role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
        return RedirectResponse(url="/reports", status_code=302)

    permissions = PermissionChecker(user.role)
    report_service = get_report_service()

    # Get templates
    templates_list, total = await report_service.get_templates(
        db,
        page=page,
        page_size=20,
    )

    total_pages = (total + 19) // 20

    context = {
        "request": request,
        "user": user,
        "templates": templates_list,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("reports/templates/manage.html", context)


@router.get("/templates/create", response_class=HTMLResponse)
async def templates_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the template creation page (admin only)."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Only admins can create templates
    if user.role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
        return RedirectResponse(url="/reports/templates/manage", status_code=302)

    permissions = PermissionChecker(user.role)

    context = {
        "request": request,
        "user": user,
        "template": None,  # Creating new template
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("reports/templates/editor.html", context)


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
async def templates_edit(
    request: Request,
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the template edit page (admin only)."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Only admins can edit templates
    if user.role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
        return RedirectResponse(url="/reports/templates/manage", status_code=302)

    permissions = PermissionChecker(user.role)
    report_service = get_report_service()

    # Get the template
    template = await report_service.get_template(db, template_id)
    if not template:
        return RedirectResponse(url="/reports/templates/manage", status_code=302)

    context = {
        "request": request,
        "user": user,
        "template": template,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("reports/templates/editor.html", context)
