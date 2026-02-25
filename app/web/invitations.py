"""Parent invitation web routes for HTML pages."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models import Student, User
from app.models.user import Role
from app.services.auth_service import get_auth_service
from app.services.class_service import get_class_service
from app.services.invitation_service import get_invitation_service
from app.services.student_service import get_student_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)
from app.web.helpers import get_teacher_class_context

router = APIRouter(prefix="/invitations")


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
async def invitations_list(
    request: Request,
    status: str | None = None,
    student_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the invitations list page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    permissions = PermissionChecker(user.role)

    if not permissions.can_invite_parents():
        raise ForbiddenException("You don't have permission to manage invitations")

    invitation_service = get_invitation_service()
    student_service = get_student_service()

    invitations, total = await invitation_service.list_invitations(
        db,
        status=status.upper() if status else None,
        student_id=student_id,
        page=page,
        page_size=20,
    )

    # Enrich invitations with student and creator names
    enriched_invitations = []
    for inv in invitations:
        student = await db.get(Student, inv.student_id)
        creator = await db.get(User, inv.created_by)
        enriched_invitations.append({
            "id": inv.id,
            "email": inv.email,
            "invitation_code": inv.invitation_code,
            "status": inv.status,
            "expires_at": inv.expires_at,
            "accepted_at": inv.accepted_at,
            "created_at": inv.created_at,
            "student_id": inv.student_id,
            "student_name": f"{student.first_name} {student.last_name}" if student else "Unknown",
            "created_by_name": f"{creator.first_name} {creator.last_name}" if creator else "Unknown",
        })

    total_pages = (total + 19) // 20

    # Get students for the create invitation dialog
    students, _ = await student_service.get_students(db, page=1, page_size=500)

    context = {
        "request": request,
        "user": user,
        "invitations": enriched_invitations,
        "students": students,
        "current_status": status,
        "current_student_id": str(student_id) if student_id else None,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "current_language": get_current_language(),
        "permissions": permissions,
    }
    if user.role == Role.TEACHER.value:
        context.update(await get_teacher_class_context(request, db))
    return templates.TemplateResponse("invitations/list.html", context)
