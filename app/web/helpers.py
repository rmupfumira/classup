"""Shared helpers for web routes."""

import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.school_class import SchoolClass, TeacherClass
from app.services.class_service import get_class_service
from app.utils.tenant_context import get_current_user_id, get_current_user_role


async def get_teacher_class_context(
    request: Request,
    db: AsyncSession,
) -> dict:
    """Get teacher's class context from the selected_class_id cookie.

    Returns a dict with:
        - teacher_classes: list of SchoolClass objects the teacher is assigned to
        - selected_class: the currently selected SchoolClass, or None
        - selected_class_id: UUID of the selected class, or None
        - is_subject_only_for_selected: True when the teacher is on this
          class ONLY as a subject specialist (ClassSubjectTeacher) and
          not a general class teacher (TeacherClass). Attendance writes
          are gated server-side on TeacherClass, so UI that offers
          attendance should hide those CTAs when this flag is True.
        - general_class_ids: set of class ids the teacher is a general
          teacher of (via TeacherClass). Templates can gate per-class
          CTAs with ``cls.id in general_class_ids``.

    If no valid class is selected, defaults to the teacher's primary class
    (or first assigned class).
    """
    role = get_current_user_role()
    if role != "TEACHER":
        return {}

    class_service = get_class_service()
    try:
        my_classes = await class_service.get_my_classes(db)
    except Exception:
        return {
            "teacher_classes": [], "selected_class": None,
            "selected_class_id": None, "general_class_ids": set(),
            "is_subject_only_for_selected": False,
        }

    if not my_classes:
        return {
            "teacher_classes": [], "selected_class": None,
            "selected_class_id": None, "general_class_ids": set(),
            "is_subject_only_for_selected": False,
        }

    # Read cookie
    cookie_val = request.cookies.get("selected_class_id")
    selected_class = None

    if cookie_val:
        try:
            cookie_uuid = uuid.UUID(cookie_val)
            for cls in my_classes:
                if cls.id == cookie_uuid:
                    selected_class = cls
                    break
        except (ValueError, AttributeError):
            pass

    user_id = get_current_user_id()

    # Classes this teacher is a GENERAL teacher of (TeacherClass). The
    # dashboard uses this to tell "has primary responsibility" from
    # "only teaches a subject here". Attendance writes gate on this
    # server-side, so matching the UX avoids dead-end CTAs.
    tc_rows = (await db.execute(
        select(TeacherClass).where(TeacherClass.teacher_id == user_id)
    )).scalars().all()
    general_class_ids: set = {tc.class_id for tc in tc_rows}
    primary_assignment = None
    for tc in tc_rows:
        if tc.is_primary:
            if primary_assignment is None or tc.assigned_at < primary_assignment.assigned_at:
                primary_assignment = tc

    # If no valid selection, pick primary class or first class
    if selected_class is None:
        if primary_assignment:
            for cls in my_classes:
                if cls.id == primary_assignment.class_id:
                    selected_class = cls
                    break

        # Fallback to first class
        if selected_class is None:
            selected_class = my_classes[0]

    is_subject_only_for_selected = bool(
        selected_class is not None
        and selected_class.id not in general_class_ids
    )

    return {
        "teacher_classes": my_classes,
        "selected_class": selected_class,
        "selected_class_id": selected_class.id if selected_class else None,
        "general_class_ids": general_class_ids,
        "is_subject_only_for_selected": is_subject_only_for_selected,
    }
