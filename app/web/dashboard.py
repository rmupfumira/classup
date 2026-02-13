"""Dashboard web routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth_service import get_auth_service
from app.templates_config import templates
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
)

router = APIRouter()


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

    # Select template based on role
    template_map = {
        "SUPER_ADMIN": "dashboard/super_admin.html",
        "SCHOOL_ADMIN": "dashboard/school_admin.html",
        "TEACHER": "dashboard/teacher.html",
        "PARENT": "dashboard/parent.html",
    }

    template_name = template_map.get(role, "dashboard/teacher.html")

    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "user": user,
            "current_language": get_current_language(),
        },
    )
