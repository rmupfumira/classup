"""Message web routes for HTML pages."""

import json
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth_service import get_auth_service
from app.services.message_service import get_message_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
)
from app.web.helpers import get_teacher_class_context

router = APIRouter(prefix="/messages")


async def _get_current_user(db: AsyncSession):
    """Get the current user from the database."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    auth_service = get_auth_service()
    try:
        return await auth_service.get_current_user(db, user_id)
    except Exception:
        return None


@router.get("", response_class=HTMLResponse)
async def messages_inbox(
    request: Request,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the messages inbox page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    permissions = PermissionChecker(role)

    service = get_message_service()
    conversations, total = await service.get_conversations(db, page=page, page_size=20)

    total_pages = (total + 20 - 1) // 20 if total > 0 else 0

    # Teacher class context for nav
    class_ctx = {}
    if role == "TEACHER":
        class_ctx = await get_teacher_class_context(request, db)

    context = {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "conversations": conversations,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "permissions": permissions,
    }
    context.update(class_ctx)

    return templates.TemplateResponse("messages/inbox.html", context)


@router.get("/thread/{thread_id}", response_class=HTMLResponse)
async def messages_thread(
    request: Request,
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the conversation thread page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    permissions = PermissionChecker(role)

    service = get_message_service()

    try:
        messages_list, total = await service.get_conversation_messages(
            db, thread_id, page=1, page_size=100,
        )
    except Exception:
        return RedirectResponse(url="/messages", status_code=302)

    # Get student and other user info from the root message
    from sqlalchemy import select
    from app.models.message import Message, MessageRecipient
    from app.models.student import Student
    from app.models.user import User
    from sqlalchemy.orm import selectinload

    root_result = await db.execute(
        select(Message)
        .options(selectinload(Message.recipients))
        .where(Message.id == thread_id)
    )
    root_msg = root_result.scalar_one_or_none()

    if not root_msg:
        return RedirectResponse(url="/messages", status_code=302)

    student_result = await db.execute(select(Student).where(Student.id == root_msg.student_id))
    student = student_result.scalar_one_or_none()

    # Determine the other user
    if root_msg.sender_id == user.id:
        other_id = root_msg.recipients[0].user_id if root_msg.recipients else None
    else:
        other_id = root_msg.sender_id

    other_user = None
    if other_id:
        other_result = await db.execute(select(User).where(User.id == other_id))
        other_user = other_result.scalar_one_or_none()

    student_name = f"{student.first_name} {student.last_name}" if student else "Unknown"
    other_user_name = f"{other_user.first_name} {other_user.last_name}" if other_user else "Unknown"
    other_user_role = other_user.role if other_user else ""
    class_name = student.school_class.name if student and student.school_class else None

    thread_subject = root_msg.subject

    # Teacher class context for nav
    class_ctx = {}
    if role == "TEACHER":
        class_ctx = await get_teacher_class_context(request, db)

    context = {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "messages": messages_list,
        "thread_id": str(thread_id),
        "student_name": student_name,
        "other_user_name": other_user_name,
        "other_user_role": other_user_role.lower() if other_user_role else "",
        "class_name": class_name,
        "thread_subject": thread_subject,
        "permissions": permissions,
    }
    context.update(class_ctx)

    return templates.TemplateResponse("messages/thread.html", context)


@router.get("/compose", response_class=HTMLResponse)
async def messages_compose(
    request: Request,
    student_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Render the compose message page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    permissions = PermissionChecker(role)

    service = get_message_service()
    compose_context = await service.get_compose_context(db)

    # Teacher class context for nav
    class_ctx = {}
    if role == "TEACHER":
        class_ctx = await get_teacher_class_context(request, db)

    context = {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "compose_data": compose_context,
        "compose_data_json": json.dumps(compose_context),
        "preselected_student_id": str(student_id) if student_id else None,
        "permissions": permissions,
    }
    context.update(class_ctx)

    return templates.TemplateResponse("messages/compose.html", context)
