"""Global search API — JSON autocomplete + results.

One endpoint, dispatches on the caller's role (SearchService does the
scoping). Powers the navbar search box and the /search results page.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import APIResponse
from app.services.search_service import get_search_service
from app.utils.tenant_context import (
    get_current_user_id,
    get_current_user_role,
    get_tenant_id_or_none,
)

router = APIRouter()


@router.get("/search")
async def global_search(
    q: str = Query("", max_length=100, description="Search query"),
    limit: int = Query(5, ge=1, le=20, description="Items per group"),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Role-scoped global search.

    Returns zero or more groups (Students, Classes, …), each with up
    to ``limit`` items. Empty query or <2 chars returns an empty
    ``groups`` list — no error, so the client can call eagerly as
    the user types without having to pre-check.
    """
    service = get_search_service()
    payload = await service.search(
        db,
        q=q,
        role=get_current_user_role() or "",
        user_id=get_current_user_id(),
        tenant_id=get_tenant_id_or_none(),
        limit_per_group=limit,
    )
    return APIResponse(status="success", data=payload)
