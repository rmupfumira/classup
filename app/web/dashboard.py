"""Dashboard web routes."""

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth_service import get_auth_service
from app.services.attendance_service import get_attendance_service
from app.services.report_service import get_report_service
from app.services.academic_service import get_academic_service
from app.services.announcement_service import get_announcement_service
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

    # Get classes with student counts and attendance in batch (2 queries instead of 2N)
    from sqlalchemy import outerjoin, literal_column
    from app.models.school_class import SchoolClass as SC

    classes_q = (
        select(SC)
        .where(SC.tenant_id == tenant_id, SC.deleted_at.is_(None), SC.is_active == True)
        .order_by(SC.name)
    )
    classes = list((await db.execute(classes_q)).scalars().all())
    class_ids = [c.id for c in classes]

    # Batch: student counts per class
    student_counts_map = {}
    if class_ids:
        sc_q = (
            select(Student.class_id, func.count(Student.id))
            .where(
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
                Student.is_active == True,
                Student.class_id.in_(class_ids),
            )
            .group_by(Student.class_id)
        )
        for row in (await db.execute(sc_q)).all():
            student_counts_map[row[0]] = row[1]

    # Batch: present counts per class today
    present_counts_map = {}
    if class_ids:
        pc_q = (
            select(AttendanceRecord.class_id, func.count(AttendanceRecord.id))
            .where(
                AttendanceRecord.tenant_id == tenant_id,
                AttendanceRecord.date == today,
                AttendanceRecord.status.in_(["PRESENT", "LATE"]),
                AttendanceRecord.class_id.in_(class_ids),
            )
            .group_by(AttendanceRecord.class_id)
        )
        for row in (await db.execute(pc_q)).all():
            present_counts_map[row[0]] = row[1]

    class_attendance = []
    for school_class in classes:
        class_attendance.append({
            "name": school_class.name,
            "age_group": school_class.age_group or school_class.grade_level or "",
            "present": present_counts_map.get(school_class.id, 0),
            "total": student_counts_map.get(school_class.id, 0),
        })

    # Get active announcements for admin (all classes)
    active_announcements = []
    try:
        user_id = get_current_user_id()
        all_class_ids = [c.id for c in classes]
        announcement_service = get_announcement_service()
        active_announcements = await announcement_service.get_active_announcements(
            db, user_id, all_class_ids,
        )
    except Exception:
        pass

    return {
        "total_students": total_students,
        "present_today": present_today,
        "absent_today": absent_today,
        "pending_reports": 0,  # TODO: implement pending reports count
        "class_attendance": class_attendance,
        "active_announcements": active_announcements,
    }


async def _get_parent_dashboard_data(db: AsyncSession, user_id, request: Request):
    """Fetch parent dashboard data using the three-section aggregator.

    Replaces the per-child dump-everything view with ranked
    needs-attention / this-week / quick-action buckets. The parent
    picks a selected child via `?child_id=` on the URL; without one we
    default to the first child alphabetically (handled inside the
    aggregator).
    """
    from app.services.parent_dashboard_service import build_parent_dashboard

    tenant_id = get_tenant_id()
    today = date.today()

    # Which child is selected (multi-child parents)
    selected_child_id = None
    raw = request.query_params.get("child_id")
    if raw:
        import uuid as _uuid
        try:
            selected_child_id = _uuid.UUID(raw)
        except (ValueError, TypeError):
            selected_child_id = None

    dashboard = await build_parent_dashboard(
        db, user_id, tenant_id, selected_child_id,
    )

    return {
        "parent_dashboard": dashboard,
        "today": today,
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

    # Get active announcements for teacher's classes
    active_announcements = []
    try:
        announcement_service = get_announcement_service()
        teacher_class_ids = await announcement_service._get_teacher_class_ids(db, user_id)
        active_announcements = await announcement_service.get_active_announcements(
            db, user_id, teacher_class_ids,
        )
    except Exception:
        pass

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
        "active_announcements": active_announcements,
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
        context["active_announcements"] = stats.get("active_announcements", [])

        # Add subscription status for trial/billing banner
        try:
            from app.services.subscription_service import get_subscription_service
            sub_service = get_subscription_service()
            tenant_id = get_tenant_id()
            subscription = await sub_service.get_tenant_subscription(db, tenant_id)
            if subscription:
                sub_info = {
                    "status": subscription.status,
                    "trial_end": subscription.trial_end,
                    "current_period_end": subscription.current_period_end,
                    "plan_name": None,
                }
                if subscription.plan_id:
                    plan = await sub_service.get_plan(db, subscription.plan_id)
                    if plan:
                        sub_info["plan_name"] = plan.name
                        sub_info["price_monthly"] = float(plan.price_monthly)
                # Calculate days remaining for trial
                if subscription.status == "TRIALING" and subscription.trial_end:
                    days_left = (subscription.trial_end - date.today()).days
                    sub_info["trial_days_remaining"] = max(0, days_left)
                context["subscription"] = sub_info
        except Exception as e:
            logger.exception(f"Failed to check subscription for dashboard banner: {e}")

    # Add teacher-specific data
    elif role == "TEACHER":
        class_ctx = await get_teacher_class_context(request, db)
        context.update(class_ctx)
        selected_class = class_ctx.get("selected_class")
        teacher_data = await _get_teacher_dashboard_data(db, user_id, selected_class)
        context.update(teacher_data)

    # Add parent-specific data
    elif role == "PARENT":
        parent_data = await _get_parent_dashboard_data(db, user_id, request)
        context.update(parent_data)

        # Get tenant info for the school contact card
        from app.models import Tenant
        tenant_id = get_tenant_id()
        tenant = await db.get(Tenant, tenant_id)
        context["tenant"] = tenant

    return templates.TemplateResponse(template_name, context)
