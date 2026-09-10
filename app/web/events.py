"""School events + parent RSVPs — HTML pages.

Two audiences share the same URL (``/events``) and template:
  - Staff (SCHOOL_ADMIN, TEACHER): sees the full list, can create /
    edit / cancel, sees RSVP counts.
  - Parents: sees only events they're invited to; can RSVP with a tap.

The template branches on ``user.role`` so we don't fork the URL space.

Also exposes the PUBLIC signed-link RSVP endpoint that email invites
click through to — no login required, HMAC-verified.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import SchoolEvent, Tenant, User
from app.services import event_service
from app.services.auth_service import get_auth_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events")


async def _current_user(db: AsyncSession) -> User | None:
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    try:
        return await get_auth_service().get_current_user(db, user_id)
    except Exception:
        return None


@router.get("", response_class=HTMLResponse)
async def events_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List page. Same URL for staff + parent; template branches on
    user.role."""
    user = await _current_user(db)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id) if tenant_id else None

    # Fire the reminder scan on every /events load. It's idempotent
    # (reminder_Xh_sent_at flags gate double-sends) and cheap — one
    # SELECT per window plus one send per due event. Piggybacks on
    # real traffic so we don't need a separate arq worker for now.
    try:
        await event_service.send_due_reminders(db)
        await db.commit()
    except Exception:
        logger.exception("Event reminder scan failed on /events load")

    # Class list — used by the "scope = CLASS" picker on the create form.
    # get_classes reads tenant_id from the request context internally.
    from app.services.class_service import get_class_service
    classes: list = []
    if user.role in ("SCHOOL_ADMIN", "TEACHER"):
        try:
            classes, _total = await get_class_service().get_classes(
                db, is_active=True, page=1, page_size=200,
            )
        except Exception:
            classes = []

    return templates.TemplateResponse(
        "events/index.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "classes": classes,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(get_current_user_role()),
        },
    )


@router.get("/{event_id}/rsvp", response_class=HTMLResponse)
async def rsvp_click(
    event_id: uuid.UUID,
    request: Request,
    r: str = Query(..., description="YES, NO, or MAYBE"),
    u: uuid.UUID = Query(..., description="User (parent) ID from the email link"),
    sig: str = Query(..., description="HMAC signature — verifies the click came from our email"),
    db: AsyncSession = Depends(get_db),
):
    """Public one-tap RSVP endpoint the email links point to.

    HMAC signature over (event_id, user_id, response) proves the click
    came from the email we sent — no login required. Parents can also
    hit /events/{id} and RSVP through the UI if they'd rather log in
    first.

    Returns a small HTML page confirming the response (or explaining
    the error). Deliberately doesn't expose whether the signature or
    the event was the reason for failure — collapses to a generic
    "link expired" message so an attacker can't probe.
    """
    response = (r or "").upper().strip()
    if response not in {"YES", "NO", "MAYBE"}:
        return templates.TemplateResponse(
            "events/rsvp_landing.html",
            {"request": request, "success": False,
             "message": "That RSVP option isn't recognised."},
            status_code=400,
        )

    if not event_service.verify_rsvp_token(event_id, u, response, sig):
        # Signature failure. Could be forged / expired / typo — same
        # opaque error either way. Log for ops but don't leak which
        # component rejected it.
        logger.info(
            "RSVP click rejected — sig verify failed. event=%s user=%s response=%s",
            event_id, u, response,
        )
        return templates.TemplateResponse(
            "events/rsvp_landing.html",
            {"request": request, "success": False,
             "message": "This RSVP link is invalid or has expired. Log in to RSVP from the events page."},
            status_code=400,
        )

    # Load event to check tenant + cancelled state. We can't use
    # get_event's tenant_id filter here because this route isn't
    # authenticated — the sig is the whole authorisation. Load directly.
    event = await db.get(SchoolEvent, event_id)
    if event is None or event.deleted_at is not None:
        return templates.TemplateResponse(
            "events/rsvp_landing.html",
            {"request": request, "success": False,
             "message": "This event no longer exists."},
            status_code=404,
        )
    if event.cancelled_at is not None:
        return templates.TemplateResponse(
            "events/rsvp_landing.html",
            {"request": request, "success": False,
             "message": "This event has been cancelled.",
             "event": event, "when_display": event_service.format_event_when(event)},
            status_code=400,
        )

    try:
        await event_service.set_rsvp(
            db, event_id=event_id, user_id=u, response=response,
        )
        await db.commit()
    except Exception:
        logger.exception("Failed to record RSVP from email link: event=%s user=%s", event_id, u)
        return templates.TemplateResponse(
            "events/rsvp_landing.html",
            {"request": request, "success": False,
             "message": "Sorry — something went wrong recording your response. Try again in a moment."},
            status_code=500,
        )

    labels = {"YES": "attending", "NO": "not attending", "MAYBE": "maybe attending"}
    return templates.TemplateResponse(
        "events/rsvp_landing.html",
        {
            "request": request,
            "success": True,
            "message": f"Thanks — you're marked as {labels[response]}.",
            "event": event,
            "when_display": event_service.format_event_when(event),
            "response": response,
        },
    )


@router.get("/{event_id}", response_class=HTMLResponse)
async def event_detail(
    event_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Single-event view. Parent RSVP happens via the API from here."""
    user = await _current_user(db)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id) if tenant_id else None

    return templates.TemplateResponse(
        "events/detail.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "event_id": event_id,
            "current_language": get_current_language(),
            "permissions": PermissionChecker(get_current_user_role()),
        },
    )
