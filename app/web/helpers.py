"""Shared helpers for web routes."""

import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.school_class import SchoolClass
from app.services.class_service import get_class_service
from app.utils.tenant_context import get_current_user_role


async def get_teacher_class_context(
    request: Request,
    db: AsyncSession,
) -> dict:
    """Get teacher's class context from the selected_class_id cookie.

    Returns a dict with:
        - teacher_classes: list of SchoolClass objects the teacher is assigned to
        - selected_class: the currently selected SchoolClass, or None
        - selected_class_id: UUID of the selected class, or None

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
        return {"teacher_classes": [], "selected_class": None, "selected_class_id": None}

    if not my_classes:
        return {"teacher_classes": [], "selected_class": None, "selected_class_id": None}

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

    # If no valid selection, pick primary class or first class
    if selected_class is None:
        # Look for a primary assignment. Use .first() rather than
        # scalar_one_or_none() because data can occasionally have more
        # than one primary per teacher (e.g. migrated data or older
        # versions that didn't enforce uniqueness) and we should not
        # 500 the whole dashboard over it.
        import logging

        from app.models.school_class import TeacherClass
        from app.utils.tenant_context import get_current_user_id
        from sqlalchemy import select

        logger = logging.getLogger(__name__)

        user_id = get_current_user_id()
        primary_query = (
            select(TeacherClass)
            .where(
                TeacherClass.teacher_id == user_id,
                TeacherClass.is_primary == True,  # noqa: E712
            )
            .order_by(TeacherClass.assigned_at.asc())
        )
        result = await db.execute(primary_query)
        primary_rows = list(result.scalars().all())
        if len(primary_rows) > 1:
            logger.warning(
                "Teacher %s has %d primary class assignments — picking the earliest. "
                "Consider cleaning up duplicates.",
                user_id, len(primary_rows),
            )
        primary_assignment = primary_rows[0] if primary_rows else None

        if primary_assignment:
            for cls in my_classes:
                if cls.id == primary_assignment.class_id:
                    selected_class = cls
                    break

        # Fallback to first class
        if selected_class is None:
            selected_class = my_classes[0]

    return {
        "teacher_classes": my_classes,
        "selected_class": selected_class,
        "selected_class_id": selected_class.id if selected_class else None,
    }
