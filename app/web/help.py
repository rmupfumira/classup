"""Help / user-guide web routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.help_content import get_related_topics, get_topic, get_topics_for_role
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)

router = APIRouter(prefix="/help")


async def _get_current_user(db: AsyncSession) -> User | None:
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    try:
        return await get_auth_service().get_current_user(db, user_id)
    except Exception:
        return None


def _require_auth(request: Request):
    if not get_current_user_id_or_none():
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie("access_token")
        return response
    return None


@router.get("", response_class=HTMLResponse)
async def help_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Render the help index — topic cards grouped by category."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    topics = get_topics_for_role(user.role)

    # Group topics by category, preserving insertion order
    grouped: dict[str, list[dict]] = {}
    for t in topics:
        grouped.setdefault(t["category"], []).append(t)

    return templates.TemplateResponse(
        "help/index.html",
        {
            "request": request,
            "user": user,
            "grouped_topics": grouped,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )


@router.get("/{slug}", response_class=HTMLResponse)
async def help_topic(
    request: Request, slug: str, db: AsyncSession = Depends(get_db)
):
    """Render a single help topic."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    topic = get_topic(slug)
    if not topic:
        raise HTTPException(status_code=404, detail="Help topic not found")

    # Role-gate: super_admin topics are hidden from school admins
    role_key = "super_admin" if user.role == Role.SUPER_ADMIN.value else "school_admin"
    if role_key not in topic["roles"]:
        raise HTTPException(status_code=404, detail="Help topic not found")

    related = get_related_topics(topic.get("related") or [])
    # Filter related topics by role too
    related = [
        r for r in related if role_key in r["roles"]
    ]

    return templates.TemplateResponse(
        "help/topic.html",
        {
            "request": request,
            "user": user,
            "topic": topic,
            "related": related,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(user.role),
        },
    )
