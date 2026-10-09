"""Public unsubscribe endpoint for the ``List-Unsubscribe`` header.

Gmail / Yahoo send a POST (``List-Unsubscribe-Post`` one-click) and
some clients open the URL in a browser (GET). Both need to work
without login, which is why this router sits above AuthMiddleware's
exempt list (see ``app/middleware/auth.py``).

The response is deliberately simple — Gmail only looks at the HTTP
status code (200 = success), while a human opening the link gets a
confirmation page.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.templates_config import templates

logger = logging.getLogger(__name__)

router = APIRouter()


async def _flip_opt_out(db: AsyncSession, email: str) -> bool:
    """Mark every user row with this email as opted-out.

    Returns True if any row was changed, False if the address is
    unknown. Multi-tenant means one email can map to several users
    (parent at two schools, admin + teacher at same school) — flip
    them all so the parent only has to click once.
    """
    normalised = (email or "").strip().lower()
    if not normalised or "@" not in normalised:
        return False
    result = await db.execute(
        select(User).where(User.email == normalised, User.deleted_at.is_(None))
    )
    users = result.scalars().all()
    if not users:
        return False
    for user in users:
        user.email_opted_in = False
        # Flip WhatsApp off too — a parent clicking "unsubscribe" in
        # an email almost always means "stop all bulk nudges" rather
        # than "just stop email". They can re-opt-in from Profile.
        user.whatsapp_opted_in = False
    await db.commit()
    logger.info("Opt-out via List-Unsubscribe applied to %d row(s) for %s", len(users), normalised)
    return True


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_get(
    request: Request,
    email: Annotated[str, Query(..., description="recipient email")],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HTMLResponse:
    """Confirmation page for a human clicking the unsubscribe URL."""
    changed = await _flip_opt_out(db, email)
    return templates.TemplateResponse(
        "unsubscribe.html",
        {"request": request, "email": email, "changed": changed},
    )


@router.post("/unsubscribe")
async def unsubscribe_post(
    email: Annotated[str, Query(..., description="recipient email")],
    db: Annotated[AsyncSession, Depends(get_db)],
    list_unsubscribe: Annotated[str | None, Form(alias="List-Unsubscribe")] = None,
) -> dict[str, str]:
    """One-click POST handler for Gmail / Yahoo.

    Gmail sends ``List-Unsubscribe=One-Click`` as a form body; we
    ignore the value (its presence is what matters) and act on the
    email query string. Must return 200 OK for Gmail to show the
    "you've been unsubscribed" banner.
    """
    await _flip_opt_out(db, email)
    return {"status": "ok"}
