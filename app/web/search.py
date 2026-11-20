"""Global search results page.

The navbar box's Enter key lands here with ?q=… so users have a full
page they can refine and bookmark. Fetches the role-scoped results
from SearchService with a larger per-group limit than the navbar
dropdown uses.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.auth_service import get_auth_service
from app.services.search_service import get_search_service
from app.templates_config import templates
from app.utils.tenant_context import (
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id_or_none,
)

router = APIRouter()


async def _get_current_user(db: AsyncSession) -> User | None:
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    try:
        return await get_auth_service().get_current_user(db, user_id)
    except Exception:
        return None


def _require_auth(request: Request):
    """Preserve ?next= so a signed-out user lands back on their search."""
    if not get_current_user_id_or_none():
        next_url = str(request.url.path)
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        return RedirectResponse(
            url=f"/login?next={next_url}", status_code=302,
        )
    return None


@router.get("/search", response_class=HTMLResponse)
async def search_results(
    request: Request,
    q: str = Query("", max_length=100),
    db: AsyncSession = Depends(get_db),
):
    """Render search results page for the typed query."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    service = get_search_service()
    payload = await service.search(
        db,
        q=q,
        role=get_current_user_role() or "",
        user_id=user.id,
        tenant_id=get_tenant_id_or_none(),
        limit_per_group=20,  # roomier on the dedicated page
    )

    return templates.TemplateResponse(
        "search/results.html",
        {
            "request": request,
            "user": user,
            "query": payload["query"],
            "groups": payload["groups"],
            "total": payload["total"],
        },
    )
