"""Messages web routes for HTML pages."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.message_service import get_message_service
from app.services.student_service import get_student_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)

router = APIRouter(prefix="/messages")


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
async def messages_inbox(
    request: Request,
    message_type: str | None = None,
    is_read: bool | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the messages inbox page.

    Shows messages where the user is a recipient.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    message_service = get_message_service()

    # Get inbox messages
    messages, total = await message_service.get_inbox(
        db,
        message_type=message_type,
        is_read=is_read,
        page=page,
        page_size=20,
    )

    total_pages = (total + 19) // 20

    # Get unread counts
    unread_counts = await message_service.get_unread_count(db)

    return templates.TemplateResponse(
        "messages/inbox.html",
        {
            "request": request,
            "user": user,
            "messages": messages,
            "unread_counts": unread_counts,
            "current_type": message_type,
            "current_is_read": is_read,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/sent", response_class=HTMLResponse)
async def messages_sent(
    request: Request,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the sent messages page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    message_service = get_message_service()

    # Get sent messages
    messages, total = await message_service.get_sent_messages(db, page=page, page_size=20)

    total_pages = (total + 19) // 20

    return templates.TemplateResponse(
        "messages/sent.html",
        {
            "request": request,
            "user": user,
            "messages": messages,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/announcements", response_class=HTMLResponse)
async def messages_announcements(
    request: Request,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the announcements page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    message_service = get_message_service()

    # Get announcements
    messages, total = await message_service.get_announcements(db, page=page, page_size=20)

    total_pages = (total + 19) // 20

    return templates.TemplateResponse(
        "messages/announcements.html",
        {
            "request": request,
            "user": user,
            "messages": messages,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/compose", response_class=HTMLResponse)
async def messages_compose(
    request: Request,
    message_type: str | None = None,
    class_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Render the compose message page.

    Pre-fills recipient information based on query params.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    # Only staff can compose new messages
    if user.role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value, Role.TEACHER.value):
        raise ForbiddenException("You don't have permission to compose messages")

    class_service = get_class_service()
    student_service = get_student_service()

    # Get classes for the dropdown
    if user.role == Role.TEACHER.value:
        classes = await class_service.get_my_classes(db)
    else:
        classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    # Get students if a class is selected
    students = []
    if class_id:
        students, _ = await student_service.get_students(
            db, class_id=class_id, is_active=True, page_size=100
        )
    elif student_id:
        # Get the specific student
        try:
            student = await student_service.get_student(db, student_id)
            students = [student]
        except Exception:
            pass

    # Pre-selected student
    selected_student = None
    if student_id:
        try:
            selected_student = await student_service.get_student(db, student_id)
        except Exception:
            pass

    # Pre-selected class
    selected_class = None
    if class_id:
        try:
            selected_class = await class_service.get_class(db, class_id)
        except Exception:
            pass

    return templates.TemplateResponse(
        "messages/compose.html",
        {
            "request": request,
            "user": user,
            "classes": classes,
            "students": students,
            "selected_class": selected_class,
            "selected_student": selected_student,
            "message_type": message_type,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/{message_id}", response_class=HTMLResponse)
async def message_thread(
    request: Request,
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render a message thread.

    Shows the original message and all replies.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)
    message_service = get_message_service()

    # Get the message thread
    original, replies = await message_service.get_thread(db, message_id)
    await db.commit()  # Commit the read status update

    return templates.TemplateResponse(
        "messages/thread.html",
        {
            "request": request,
            "user": user,
            "original": original,
            "replies": replies,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )
