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
from app.web._parent_context import (
    get_parent_child_context,
    remember_child,
    require_login,
)

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


async def _resolve_grading_system(
    db: AsyncSession, *, template=None, class_id: uuid.UUID | None = None,
):
    """Resolve the grading scale to use for a report card, matching the
    view_report_card.html chain so the edit / create / view screens all
    agree. Priority:
      1. Template's explicit grading_system_id (most specific).
      2. Class's grading system (set by curriculum inferrer or admin).
      3. Tenant default.
    Returned object has `.name` and `.grades` (JSONB list of
    {min,max,grade,description[,points]}).
    """
    from app.services.academic_service import get_academic_service
    academic_service = get_academic_service()
    gs = None
    tpl_id = getattr(template, "grading_system_id", None) if template else None
    if tpl_id:
        gs = await academic_service.get_grading_system(db, tpl_id)
    if not gs and class_id:
        gs = await academic_service.resolve_class_grading_system(db, class_id)
    if not gs:
        gs = await academic_service.get_default_grading_system(db)
    return gs


def _require_auth(request: Request):
    """Check authentication; redirect to login keeping ``?next=``."""
    return require_login(request)


async def _term_labels(db: AsyncSession, reports: list[dict]) -> dict:
    """{report_id: "Term 2 2026"} for report cards / progress reports,
    resolved from the jurisdiction school calendar (P-ACAD-02). A report
    dated outside every term (or in a year with no calendar) gets its
    month ("August 2026") instead; daily reports get no label."""
    out: dict = {}
    try:
        from app.models import Tenant
        from app.services import calendar_service
        from app.utils.tenant_context import get_tenant_id
        tenant = await db.get(Tenant, get_tenant_id())
        country = str(((tenant.settings if tenant else None) or {}).get("country") or "ZA").upper()
        cals: dict[int, object] = {}
        for r in reports:
            d = r.get("report_date")
            if not d or r.get("report_type") not in ("REPORT_CARD", "PROGRESS_REPORT"):
                continue
            if d.year not in cals:
                cals[d.year] = await calendar_service.get_calendar(db, country, d.year)
            cal = cals[d.year]
            term = cal.term_for(d) if cal is not None else None
            # Only label a term when the date really falls inside that
            # term on the tenant's calendar; otherwise (holidays, years
            # without a calendar) say the month — never guess a term.
            out[r["id"]] = f"{term.name} {d.year}" if term is not None else d.strftime("%B %Y")
    except Exception:
        pass
    return out


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

    # For teachers, default to selected class when no class_id provided
    teacher_ctx = {}
    if user.role == Role.TEACHER.value:
        teacher_ctx = await get_teacher_class_context(request, db)
        if class_id is None and teacher_ctx.get("selected_class_id"):
            class_id = teacher_ctx["selected_class_id"]

    # Get classes for filter dropdown
    if user.role == Role.TEACHER.value:
        classes = await class_service.get_my_classes(db)
    elif user.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)
    else:
        classes = []

    # Parents: one child at a time (validated selection), published
    # reports only (enforced by the service), no status / date filters.
    child_ctx: dict = {}
    page_size = 20
    if user.role == Role.PARENT.value:
        child_ctx = await get_parent_child_context(request, db, explicit=student_id)
        student_id = child_ctx.get("selected_child_id")
        class_id = None
        report_date = None
        status = None
        page_size = 100
        if student_id is None:
            # No linked children → nothing to list (never fall through
            # to an unfiltered query).
            context = {
                "request": request, "user": user, "reports": [], "classes": [],
                "current_class_id": None, "current_student_id": None,
                "current_date": None, "current_status": None, "page": 1,
                "total_pages": 0, "total": 0, "report_years": [], "term_labels": {},
                "current_language": get_current_language(), "permissions": permissions,
                **child_ctx,
            }
            return templates.TemplateResponse("reports/list.html", context)

    # Get reports
    reports, total = await report_service.get_reports(
        db,
        class_id=class_id,
        student_id=student_id,
        report_date=report_date,
        status=status,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    # Parent view groups by school year and labels terms (P-ACAD-02).
    report_years: list = []
    term_labels: dict = {}
    if child_ctx:
        term_labels = await _term_labels(db, reports)
        for r in reports:
            y = r["report_date"].year if r.get("report_date") else None
            if not report_years or report_years[-1][0] != y:
                report_years.append((y, []))
            report_years[-1][1].append(r)

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
        "report_years": report_years,
        "term_labels": term_labels,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if teacher_ctx:
        context.update(teacher_ctx)
    if child_ctx:
        context.update(child_ctx)
        response = templates.TemplateResponse("reports/list.html", context)
        remember_child(response, child_ctx)
        return response
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

    # Resolve the live grading scale — same chain the view/PDF uses —
    # so the create page's live grade computation matches the parent's
    # final view. Guard with try/except so a missing grading system
    # never blocks report creation.
    grading_system = None
    try:
        class_id_for_grading = (
            selected_student.class_id if selected_student and selected_student.class_id else None
        )
        if selected_template or class_id_for_grading:
            grading_system = await _resolve_grading_system(
                db, template=selected_template, class_id=class_id_for_grading,
            )
    except Exception:
        grading_system = None

    today = date.today()
    context = {
        "request": request,
        "user": user,
        "classes": classes,
        "templates": templates_list,
        "selected_student": selected_student,
        "selected_template": selected_template,
        "grading_system": grading_system,
        "report_date": report_date or today,
        "today": today,
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

    # get_report is scoped to the viewer (teacher: own classes, parent:
    # own children's finalised reports), so out-of-scope ids land here.
    report = await report_service.get_report(db, report_id)
    if not report:
        return RedirectResponse(url="/reports", status_code=302)

    # Get tenant for branding on report cards
    from app.models import Tenant
    from app.utils.tenant_context import get_tenant_id
    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    # Use report card template for REPORT_CARD type reports
    template_name = "reports/view.html"
    grading_system = None
    class_subjects = []
    subject_grading_map: dict[str, list[dict]] = {}

    # Resolve term + year for REPORT_CARD / PROGRESS_REPORT so the web
    # view can fall back to the jurisdiction calendar when the admin
    # didn't type a term/year into the student_info section. Daily
    # activity reports skip this.
    auto_term_label = ""
    auto_academic_year = ""
    report_type = (
        report.template.report_type if report.template else None
    )
    if report_type in ("REPORT_CARD", "PROGRESS_REPORT") and report.report_date:
        try:
            from app.services import calendar_service
            country_code = str(
                ((tenant.settings if tenant else None) or {}).get("country") or "ZA"
            ).upper()
            term = await calendar_service.get_term_for_date(
                db, country_code, report.report_date,
            )
            if term is not None:
                auto_term_label = term.name
                auto_academic_year = str(report.report_date.year)
        except Exception:
            # Calendar lookup is additive — never block rendering.
            pass

    if report.template and report.template.report_type == "REPORT_CARD":
        template_name = "reports/view_report_card.html"

        from app.services.academic_service import get_academic_service
        academic_service = get_academic_service()
        # Grading scale resolution lives in _resolve_grading_system so
        # create / edit / view can't drift. See the helper for priority.
        grading_system = await _resolve_grading_system(
            db, template=report.template, class_id=report.class_id,
        )

        # Get class subjects for this class
        if report.class_id:
            class_subjects = await academic_service.get_class_subjects(db, report.class_id)

        # Combined-curriculum tenants (ZIMSEC + Cambridge in one class)
        # need each subject to grade on its own body's scale. The
        # resolver returns a {subject_id → grades-list} map only for
        # subjects whose curriculum_pack_code points at a scale
        # different from the class default; empty on every single-
        # curriculum tenant so this is a no-op there.
        if class_subjects:
            subjects_for_resolve = [cs.subject for cs in class_subjects if cs.subject]
            raw_map = await academic_service.resolve_subject_grading_scales(
                db, subjects_for_resolve,
            )
            # Stringify keys so Jinja's `in` test matches `subject.id | string`.
            subject_grading_map = {str(k): v for k, v in raw_map.items()}

    context = {
        "request": request,
        "user": user,
        "report": report,
        "tenant": tenant,
        "grading_system": grading_system,
        "class_subjects": class_subjects,
        "subject_grading_map": subject_grading_map,
        "auto_term_label": auto_term_label,
        "auto_academic_year": auto_academic_year,
        # P-ACAD-03: last-resort Year label derived from the report date
        # so the card never shows a bare "-".
        "fallback_year": str(report.report_date.year) if report.report_date else "",
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    if user.role == Role.PARENT.value:
        # The report is the parent's own child's (get_report is scoped),
        # so viewing it also makes that child the current selection.
        child_ctx = await get_parent_child_context(request, db, explicit=report.student_id)
        context.update(child_ctx)
        response = templates.TemplateResponse(template_name, context)
        remember_child(response, child_ctx)
        return response
    return templates.TemplateResponse(template_name, context)


@router.get("/{report_id}/pdf")
async def report_pdf(
    request: Request,
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Download a report as PDF (P-ACAD-01).

    Loads through ``report_service.get_report`` — already scoped to the
    viewer (teacher: own classes; parent: own children's published
    reports) — so an out-of-scope or draft id redirects to /reports and
    never renders. Wraps the existing ``render_report_pdf``.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    report = await get_report_service().get_report(db, report_id)
    if not report:
        return RedirectResponse(url="/reports", status_code=302)

    import re
    from fastapi.responses import Response
    from app.services.report_pdf import render_report_pdf

    pdf_bytes = await render_report_pdf(db, report)
    student = report.student
    name = f"{student.first_name}_{student.last_name}" if student else "report"
    kind = (report.template.name if report.template else "Report")
    raw = f"{name}_{kind}_{report.report_date.isoformat() if report.report_date else ''}"
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("_")[:120] or "report"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


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

    # Load class subjects for ACADEMIC_GRADES sections
    class_subjects = []
    if report.class_id:
        try:
            from app.services.academic_service import get_academic_service
            academic_service = get_academic_service()
            class_subjects = await academic_service.get_class_subjects(db, report.class_id)
        except Exception:
            pass

    # Resolve the live grading scale the same way the view page does,
    # so the edit-time legend + computed grade matches what parents see.
    grading_system = None
    try:
        grading_system = await _resolve_grading_system(
            db, template=report.template, class_id=report.class_id,
        )
    except Exception:
        grading_system = None

    context = {
        "request": request,
        "user": user,
        "report": report,
        "class_subjects": class_subjects,
        "grading_system": grading_system,
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

    # Ensure default templates exist for this tenant
    await report_service.ensure_default_templates(db)

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
