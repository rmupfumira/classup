"""Dashboard web routes."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth_service import get_auth_service
from app.services.student_service import get_student_service
from app.services.attendance_service import get_attendance_service
from app.services.report_service import get_report_service
from app.services.academic_service import get_academic_service
from app.services.class_service import get_class_service
from app.services.notification_service import get_notification_service
from app.templates_config import templates
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id,
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id,
)
from app.web.helpers import get_teacher_class_context

router = APIRouter()


async def _get_school_admin_dashboard_data(db: AsyncSession):
    """Fetch all data needed for school admin dashboard."""
    from app.models.student import Student
    from app.models.attendance import AttendanceRecord
    from app.models.school_class import SchoolClass

    tenant_id = get_tenant_id()
    today = date.today()

    # Get total students count
    student_count_query = select(func.count()).select_from(
        select(Student).where(
            Student.tenant_id == tenant_id,
            Student.deleted_at.is_(None),
            Student.is_active == True,
        ).subquery()
    )
    total_students = (await db.execute(student_count_query)).scalar() or 0

    # Get today's attendance stats
    present_today = 0
    absent_today = 0

    if total_students > 0:
        # Count present (PRESENT or LATE)
        present_query = select(func.count()).select_from(
            select(AttendanceRecord).where(
                AttendanceRecord.tenant_id == tenant_id,
                AttendanceRecord.date == today,
                AttendanceRecord.status.in_(["PRESENT", "LATE"]),
            ).subquery()
        )
        present_today = (await db.execute(present_query)).scalar() or 0

        # Count absent
        absent_query = select(func.count()).select_from(
            select(AttendanceRecord).where(
                AttendanceRecord.tenant_id == tenant_id,
                AttendanceRecord.date == today,
                AttendanceRecord.status == "ABSENT",
            ).subquery()
        )
        absent_today = (await db.execute(absent_query)).scalar() or 0

    # Get classes with their attendance for today
    class_service = get_class_service()
    classes, _ = await class_service.get_classes(db, page=1, page_size=100)

    class_attendance = []
    for school_class in classes:
        # Get student count in this class
        class_student_count = await db.execute(
            select(func.count()).select_from(
                select(Student).where(
                    Student.tenant_id == tenant_id,
                    Student.class_id == school_class.id,
                    Student.deleted_at.is_(None),
                    Student.is_active == True,
                ).subquery()
            )
        )
        class_student_count = class_student_count.scalar() or 0

        # Get present count for this class today
        class_present_count = await db.execute(
            select(func.count()).select_from(
                select(AttendanceRecord).where(
                    AttendanceRecord.tenant_id == tenant_id,
                    AttendanceRecord.class_id == school_class.id,
                    AttendanceRecord.date == today,
                    AttendanceRecord.status.in_(["PRESENT", "LATE"]),
                ).subquery()
            )
        )
        class_present_count = class_present_count.scalar() or 0

        class_attendance.append({
            "name": school_class.name,
            "age_group": school_class.age_group or school_class.grade_level or "",
            "present": class_present_count,
            "total": class_student_count,
        })

    return {
        "total_students": total_students,
        "present_today": present_today,
        "absent_today": absent_today,
        "pending_reports": 0,  # TODO: implement pending reports count
        "class_attendance": class_attendance,
    }


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


async def _get_teacher_dashboard_data(db: AsyncSession, user_id, selected_class=None):
    """Fetch all data needed for teacher dashboard.

    If selected_class is provided, shows data for that class only.
    """
    attendance_service = get_attendance_service()
    report_service = get_report_service()
    notification_service = get_notification_service()

    today = date.today()

    # Build class info for the selected class
    class_info = None
    total_students = 0
    total_present = 0
    total_absent = 0
    attendance_taken = False

    if selected_class:
        class_info = {
            "id": str(selected_class.id),
            "name": selected_class.name,
            "grade_level": selected_class.grade_level or selected_class.age_group or "",
            "student_count": 0,
            "present": 0,
            "absent": 0,
            "late": 0,
            "excused": 0,
            "attendance_taken": False,
            "attendance_rate": 0,
        }

        try:
            att_data = await attendance_service.get_class_attendance_for_date(
                db, selected_class.id, today,
            )
            stats = att_data.get("stats", {})
            class_info["student_count"] = stats.get("total_students", 0)
            class_info["present"] = stats.get("present_count", 0)
            class_info["absent"] = stats.get("absent_count", 0)
            class_info["late"] = stats.get("late_count", 0)
            class_info["excused"] = stats.get("excused_count", 0)
            class_info["attendance_rate"] = stats.get("attendance_rate", 0)
            recorded = class_info["present"] + class_info["absent"] + class_info["late"] + class_info["excused"]
            class_info["attendance_taken"] = recorded > 0

            total_students = class_info["student_count"]
            total_present = class_info["present"]
            total_absent = class_info["absent"]
            attendance_taken = class_info["attendance_taken"]
        except Exception:
            pass

    # Get draft reports for the selected class only
    draft_reports = []
    total_drafts = 0
    if selected_class:
        try:
            reports, count = await report_service.get_reports(
                db, class_id=selected_class.id, status="DRAFT", page=1, page_size=5,
            )
            total_drafts = count
            draft_reports = list(reports)
        except Exception:
            pass

    # Get unread notification count
    unread_count = 0
    try:
        unread_count = await notification_service.get_unread_count(db, user_id)
    except Exception:
        pass

    # Time-of-day greeting
    from datetime import datetime
    hour = datetime.now().hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    return {
        "greeting": greeting,
        "today": today,
        "current_class": class_info,
        "total_students": total_students,
        "total_present": total_present,
        "total_absent": total_absent,
        "attendance_taken": attendance_taken,
        "draft_reports": draft_reports,
        "total_drafts": total_drafts,
        "unread_notifications": unread_count,
        "has_classes": selected_class is not None,
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

    # Add school admin-specific data (setup status + stats)
    if role == "SCHOOL_ADMIN":
        academic_service = get_academic_service()
        setup_status = await academic_service.get_setup_status(db)
        context["setup_status"] = setup_status

        # Add dashboard stats (real data from database)
        stats = await _get_school_admin_dashboard_data(db)
        context["stats"] = stats

    # Add teacher-specific data
    elif role == "TEACHER":
        class_ctx = await get_teacher_class_context(request, db)
        context.update(class_ctx)
        selected_class = class_ctx.get("selected_class")
        teacher_data = await _get_teacher_dashboard_data(db, user_id, selected_class)
        context.update(teacher_data)

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
