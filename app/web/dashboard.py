"""Dashboard web routes."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth_service import get_auth_service
from app.services.student_service import get_student_service
from app.services.attendance_service import get_attendance_service
from app.services.report_service import get_report_service
from app.services.academic_service import get_academic_service
from app.templates_config import templates
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
)

router = APIRouter()


async def _get_parent_dashboard_data(db: AsyncSession, user_id):
    """Fetch all data needed for parent dashboard."""
    student_service = get_student_service()
    attendance_service = get_attendance_service()
    report_service = get_report_service()

    # Get parent's children
    children = await student_service.get_my_children(db, user_id)

    # Get today's attendance for each child
    today = date.today()
    children_data = []
    recent_reports = []

    for child in children:
        # Get today's attendance
        attendance_today = None
        try:
            records, _ = await attendance_service.get_attendance_records(
                db,
                student_id=child.id,
                date_from=today,
                date_to=today,
                page=1,
                page_size=1,
            )
            if records:
                attendance_today = records[0]
        except Exception:
            pass

        # Get recent attendance (last 7 days)
        week_ago = today - timedelta(days=7)
        attendance_history = []
        try:
            records, _ = await attendance_service.get_attendance_records(
                db,
                student_id=child.id,
                date_from=week_ago,
                date_to=today,
                page=1,
                page_size=7,
            )
            attendance_history = records
        except Exception:
            pass

        # Get recent reports for this child (last 5)
        try:
            child_reports, _ = await report_service.get_student_reports(
                db,
                student_id=child.id,
                page=1,
                page_size=5,
            )
            for report in child_reports:
                report['child'] = child
                recent_reports.append(report)
        except Exception:
            pass

        children_data.append({
            'child': child,
            'attendance_today': attendance_today,
            'attendance_history': attendance_history,
        })

    # Sort reports by date (most recent first)
    recent_reports.sort(key=lambda r: r.get('report_date', ''), reverse=True)
    recent_reports = recent_reports[:10]  # Limit to 10 most recent

    return {
        'children': children_data,
        'recent_reports': recent_reports,
        'today': today,
    }


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the role-based dashboard."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        # Clear any invalid cookie and redirect to login
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie("access_token")
        return response

    # Get current user
    auth_service = get_auth_service()
    user = await auth_service.get_current_user(db, user_id)

    role = get_current_user_role()

    # Redirect super admin to their dedicated dashboard
    if role == "SUPER_ADMIN":
        return RedirectResponse(url="/admin", status_code=302)

    # Select template based on role
    template_map = {
        "SCHOOL_ADMIN": "dashboard/school_admin.html",
        "TEACHER": "dashboard/teacher.html",
        "PARENT": "dashboard/parent.html",
    }

    template_name = template_map.get(role, "dashboard/teacher.html")

    # Build context
    context = {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "timedelta": timedelta,  # Make timedelta available in templates
    }

    # Add school admin-specific data (setup status)
    if role == "SCHOOL_ADMIN":
        academic_service = get_academic_service()
        setup_status = await academic_service.get_setup_status(db)
        context["setup_status"] = setup_status

    # Add parent-specific data
    elif role == "PARENT":
        parent_data = await _get_parent_dashboard_data(db, user_id)
        context.update(parent_data)

        # Get tenant info for the school contact card
        from app.models import Tenant
        from app.utils.tenant_context import get_tenant_id
        tenant_id = get_tenant_id()
        tenant = await db.get(Tenant, tenant_id)
        context["tenant"] = tenant

    return templates.TemplateResponse(template_name, context)
