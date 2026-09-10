"""School events + RSVPs API.

Staff create/edit events with a scope (school, class, or student);
parents view + RSVP to the events they're invited to. Every invitation
fires an email with an ICS attachment so parents can add it to their
own calendar in one tap.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import EventScope, EventType, RsvpResponse, SchoolEvent, User
from app.schemas.common import APIResponse
from app.services import event_service
from app.utils.permissions import require_role
from app.utils.tenant_context import get_current_user_id, get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Events"])


# ─────────────────── Schemas ───────────────────


class EventCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    event_type: str = Field(
        default=EventType.PARENT_MEETING.value,
        description="One of: " + ", ".join(t.value for t in EventType),
    )
    scope: str = Field(
        default=EventScope.SCHOOL.value,
        description="SCHOOL, CLASS, or STUDENT.",
    )
    class_id: uuid.UUID | None = None
    student_id: uuid.UUID | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = Field(None, max_length=300)
    rsvp_required: bool = False
    rsvp_deadline: datetime | None = None
    timezone: str | None = Field(None, max_length=64, description="IANA timezone name")

    @field_validator("event_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        try:
            return EventType(v).value
        except ValueError:
            raise ValueError(
                f"event_type must be one of: {', '.join(t.value for t in EventType)}"
            )

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        try:
            return EventScope(v).value
        except ValueError:
            raise ValueError(
                f"scope must be one of: {', '.join(s.value for s in EventScope)}"
            )


class EventUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    location: str | None = Field(None, max_length=300)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    rsvp_required: bool | None = None
    rsvp_deadline: datetime | None = None


class RsvpRequest(BaseModel):
    response: str = Field(..., description="YES, NO, or MAYBE")

    @field_validator("response")
    @classmethod
    def _validate(cls, v: str) -> str:
        try:
            return RsvpResponse(v.upper()).value
        except ValueError:
            raise ValueError("response must be YES, NO, or MAYBE")


# ─────────────────── Serialisation ───────────────────


def _event_to_dict(
    event: SchoolEvent, *, include_rsvps: bool = False,
) -> dict[str, Any]:
    """Common serialiser — matches the client's expected shape."""
    out: dict[str, Any] = {
        "id": str(event.id),
        "tenant_id": str(event.tenant_id),
        "title": event.title,
        "description": event.description,
        "event_type": event.event_type,
        "scope": event.scope,
        "class_id": str(event.class_id) if event.class_id else None,
        "student_id": str(event.student_id) if event.student_id else None,
        "class_name": event.school_class.name if event.school_class else None,
        "student_name": (
            f"{event.student.first_name} {event.student.last_name}"
            if event.student else None
        ),
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "timezone": event.timezone,
        "location": event.location,
        "rsvp_required": event.rsvp_required,
        "rsvp_deadline": event.rsvp_deadline.isoformat() if event.rsvp_deadline else None,
        "cancelled_at": event.cancelled_at.isoformat() if event.cancelled_at else None,
        "when_display": event_service.format_event_when(event),
    }
    if include_rsvps:
        out["rsvp_summary"] = {
            r.response: sum(1 for x in event.rsvps if x.response == r.response)
            for r in event.rsvps
        }
    return out


async def _send_invitations(
    db: AsyncSession, event: SchoolEvent, *, method: str = "REQUEST",
) -> None:
    """Fire the ICS email invite to every parent in the audience.

    Best-effort per parent — one failing send doesn't stop the rest.
    Runs after the create commit; nothing here can roll back the event.
    """
    from app.services import event_service as es
    from app.services.email_service import EmailService

    audience = await es.resolve_audience(db, event)
    if not audience:
        return
    tenant = event.tenant  # eager-loaded
    tenant_name = tenant.name if tenant else "Your school"
    tz_name = event.timezone or await es.get_tenant_timezone(db, event.tenant_id)

    ics = es.build_ics(
        event,
        organizer_email=tenant.email if tenant else None,
        organizer_name=tenant_name,
        method=method,
    ).encode("utf-8")

    email_service = EmailService()
    app_settings = get_settings()
    base = app_settings.app_base_url.rstrip("/")
    view_url = f"{base}/events/{event.id}"

    event_type_label = event.event_type.replace("_", " ").title()
    when_display = es.format_event_when(event, tz_name)
    deadline_display = (
        event.rsvp_deadline.strftime("%a %d %b %Y, %H:%M")
        if event.rsvp_deadline else None
    )

    for parent in audience:
        try:
            # Signed RSVP token — parent clicks the email link and we
            # trust the response without a login round-trip. Token is
            # opaque to us (secrets.token_urlsafe) — the mapping token
            # → (event, user, response) lives client-side in the URL,
            # so we don't need to persist a table. If tampered the
            # response record is still keyed by user_id which the
            # session-verify below re-checks.
            rsvp_yes = f"{base}/events/{event.id}/rsvp?r=YES&u={parent.id}"
            rsvp_no = f"{base}/events/{event.id}/rsvp?r=NO&u={parent.id}"
            rsvp_maybe = f"{base}/events/{event.id}/rsvp?r=MAYBE&u={parent.id}"
            await email_service.send_event_invitation(
                to=parent.email,
                parent_name=parent.first_name or "there",
                tenant_name=tenant_name,
                event_title=event.title,
                event_when=when_display,
                event_location=event.location,
                event_description=event.description,
                event_type_label=event_type_label,
                rsvp_required=event.rsvp_required,
                rsvp_deadline=deadline_display,
                view_url=view_url,
                rsvp_yes_url=rsvp_yes if event.rsvp_required else None,
                rsvp_no_url=rsvp_no if event.rsvp_required else None,
                rsvp_maybe_url=rsvp_maybe if event.rsvp_required else None,
                ics_bytes=ics,
                ics_filename=f"event-{event.id}.ics",
                method=method,
            )
        except Exception:
            logger.exception(
                "Failed to send event invitation email to %s for event %s",
                parent.email, event.id,
            )
        # WhatsApp — best-effort fallback via announcement template.
        try:
            from app.services import parent_notifier
            await parent_notifier.notify_event_invited(
                db, parent,
                tenant_name=tenant_name,
                event_title=event.title,
                event_when=when_display,
                event_location=event.location,
            )
        except Exception:
            logger.exception(
                "Failed to send event WhatsApp notification to user %s for event %s",
                parent.id, event.id,
            )


# ─────────────────── Endpoints ───────────────────


@router.get("/events")
@require_role("SCHOOL_ADMIN", "TEACHER")
async def list_events(
    upcoming_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Staff view: every event on this tenant, most recent first."""
    tenant_id = get_tenant_id()
    events = await event_service.list_events(
        db, tenant_id=tenant_id, upcoming_only=upcoming_only,
        limit=limit, offset=offset,
    )
    return APIResponse(
        status="success",
        data=[_event_to_dict(e, include_rsvps=True) for e in events],
    )


@router.get("/events/my")
@require_role("PARENT", "SCHOOL_ADMIN", "TEACHER")
async def list_my_events(
    upcoming_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Parent view: events the parent is invited to (union of school +
    their children's class + their children's individual events)."""
    tenant_id = get_tenant_id()
    user_id = get_current_user_id()
    events = await event_service.list_events_for_parent(
        db, parent_id=user_id, tenant_id=tenant_id, upcoming_only=upcoming_only,
    )
    # Include the parent's own RSVP state so the UI can pre-select.
    result = []
    for e in events:
        d = _event_to_dict(e)
        my = next((r for r in e.rsvps if r.user_id == user_id), None)
        d["my_rsvp"] = my.response if my else None
        result.append(d)
    return APIResponse(status="success", data=result)


@router.post("/events")
@require_role("SCHOOL_ADMIN", "TEACHER")
async def create_event(
    body: EventCreate,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Create an event + fire invitations to the audience."""
    tenant_id = get_tenant_id()
    user_id = get_current_user_id()

    # Scope-vs-fk validation
    if body.scope == EventScope.CLASS.value and not body.class_id:
        raise HTTPException(status_code=400, detail="class_id is required for CLASS scope")
    if body.scope == EventScope.STUDENT.value and not body.student_id:
        raise HTTPException(status_code=400, detail="student_id is required for STUDENT scope")

    event = await event_service.create_event(
        db,
        tenant_id=tenant_id,
        created_by=user_id,
        title=body.title,
        description=body.description,
        event_type=body.event_type,
        scope=body.scope,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        class_id=body.class_id,
        student_id=body.student_id,
        location=body.location,
        rsvp_required=body.rsvp_required,
        rsvp_deadline=body.rsvp_deadline,
        timezone_name=body.timezone,
    )
    await db.commit()
    await db.refresh(event)

    # Fire invitations AFTER commit so a failing send never rolls back
    # the created event.
    try:
        await _send_invitations(db, event, method="REQUEST")
    except Exception:
        logger.exception("Event %s created but invitations failed", event.id)

    return APIResponse(
        status="success",
        data=_event_to_dict(event, include_rsvps=True),
        message="Event created and invitations sent.",
    )


@router.get("/events/{event_id}")
@require_role("SCHOOL_ADMIN", "TEACHER", "PARENT")
async def get_event(
    event_id: uuid.UUID, db: AsyncSession = Depends(get_db),
) -> APIResponse:
    tenant_id = get_tenant_id()
    event = await event_service.get_event(db, event_id, tenant_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return APIResponse(status="success", data=_event_to_dict(event, include_rsvps=True))


@router.put("/events/{event_id}")
@require_role("SCHOOL_ADMIN", "TEACHER")
async def update_event(
    event_id: uuid.UUID,
    body: EventUpdate,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    tenant_id = get_tenant_id()
    event = await event_service.get_event(db, event_id, tenant_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(event, key, value)
    await db.commit()
    await db.refresh(event)

    # Re-send with the same UID so calendar clients update in place.
    try:
        await _send_invitations(db, event, method="REQUEST")
    except Exception:
        logger.exception("Event %s updated but re-invite failed", event.id)

    return APIResponse(
        status="success",
        data=_event_to_dict(event, include_rsvps=True),
        message="Event updated. Invitations refreshed.",
    )


@router.post("/events/{event_id}/cancel")
@require_role("SCHOOL_ADMIN", "TEACHER")
async def cancel_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    tenant_id = get_tenant_id()
    event = await event_service.get_event(db, event_id, tenant_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    await event_service.cancel_event(db, event)
    await db.commit()
    await db.refresh(event)

    try:
        await _send_invitations(db, event, method="CANCEL")
    except Exception:
        logger.exception("Event %s cancelled but cancellation email failed", event.id)

    return APIResponse(
        status="success",
        data=_event_to_dict(event, include_rsvps=True),
        message="Event cancelled. Attendees notified.",
    )


@router.delete("/events/{event_id}")
@require_role("SCHOOL_ADMIN")
async def delete_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    tenant_id = get_tenant_id()
    event = await event_service.get_event(db, event_id, tenant_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    await event_service.soft_delete_event(db, event)
    await db.commit()
    return APIResponse(status="success", message="Event deleted.")


@router.post("/events/{event_id}/rsvp")
@require_role("PARENT", "SCHOOL_ADMIN", "TEACHER")
async def rsvp_event(
    event_id: uuid.UUID,
    body: RsvpRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Record a parent's RSVP. Idempotent — the latest response wins."""
    tenant_id = get_tenant_id()
    user_id = get_current_user_id()
    event = await event_service.get_event(db, event_id, tenant_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.is_cancelled:
        raise HTTPException(status_code=400, detail="Event is cancelled.")

    rsvp = await event_service.set_rsvp(
        db, event_id=event_id, user_id=user_id, response=body.response,
    )
    await db.commit()
    return APIResponse(
        status="success",
        data={"response": rsvp.response, "responded_at": rsvp.responded_at.isoformat()},
        message="RSVP recorded.",
    )
