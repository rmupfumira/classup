"""Tenant slug catch-all route.

Registered LAST in the web_router so FastAPI only tries to match this
after every other concrete route has been checked. Hits a landing page
for the tenant with its branding.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db


router = APIRouter(include_in_schema=False)


@router.get("/{slug}", response_class=HTMLResponse)
async def tenant_slug_catchall(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    """Catch-all at `/{slug}` — resolves to tenant landing or 404."""
    from app.web.auth import _render_tenant_landing

    return await _render_tenant_landing(request, slug, db)
