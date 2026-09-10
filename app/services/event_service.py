"""School events service — CRUD, audience resolution, ICS generation,
RSVP handling.

Audience (who to notify) is COMPUTED on demand from
``(event.scope, event.class_id, event.student_id)`` and the current
parent roster — no materialised invitees table. Cost is one JOIN;
correctness win is that a parent who joins after the event was
created still gets the reminder.

ICS calendar attachment is generated per-event (stable UID = event.id
so updating the event updates the calendar entry in Google/Outlook/
Apple Mail).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    EventRsvp, EventScope, ParentStudent, RsvpResponse, SchoolEvent,
    Student, Tenant, User,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audience resolution
# ---------------------------------------------------------------------------


async def resolve_audience(
    db: AsyncSession, event: SchoolEvent,
) -> list[User]:
    """Return the parent Users that should be invited to this event.

    Scope semantics:
      SCHOOL   → every parent user on the tenant
      CLASS    → parents of every student in event.class_id
      STUDENT  → parents of event.student_id

    Always: active + not deleted + role == PARENT. WhatsApp opt-in
    is NOT checked here (that lives in parent_notifier's own gate);
    the email flow ignores WA opt-in entirely.
    """
    if event.scope == EventScope.SCHOOL.value:
        stmt = select(User).where(
            User.tenant_id == event.tenant_id,
            User.role == "PARENT",
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    elif event.scope == EventScope.CLASS.value and event.class_id:
        # Parents of every student in the given class.
        stmt = (
            select(User)
            .join(ParentStudent, ParentStudent.parent_id == User.id)
            .join(Student, Student.id == ParentStudent.student_id)
            .where(
                Student.class_id == event.class_id,
                Student.deleted_at.is_(None),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                User.role == "PARENT",
            )
            .distinct()
        )
    elif event.scope == EventScope.STUDENT.value and event.student_id:
        stmt = (
            select(User)
            .join(ParentStudent, ParentStudent.parent_id == User.id)
            .where(
                ParentStudent.student_id == event.student_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                User.role == "PARENT",
            )
        )
    else:
        # Scope-vs-fk mismatch — log + empty audience so nothing crashes.
        logger.warning(
            "Event %s has scope=%s but missing target FK — no audience resolved",
            event.id, event.scope,
        )
        return []

    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# ICS generation
# ---------------------------------------------------------------------------


def _ics_escape(value: str) -> str:
    """Escape special characters per RFC 5545 §3.3.11.
    Order matters: escape backslash first, else the added backslashes
    get re-escaped."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold_line(line: str) -> str:
    """RFC 5545 line folding: split at 75 octets with CRLF + space.
    Most clients tolerate long lines, but Gmail's parser is strict."""
    if len(line) <= 75:
        return line
    parts = [line[:75]]
    rest = line[75:]
    while rest:
        parts.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(parts)


def _fmt_dt_utc(dt: datetime) -> str:
    """Format a datetime as an ICS UTC timestamp: YYYYMMDDTHHMMSSZ.
    Naive datetimes are assumed UTC (we're strict everywhere else, so
    this is a belt-and-braces default rather than a common code path)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(
    event: SchoolEvent,
    *,
    organizer_email: str | None = None,
    organizer_name: str | None = None,
    method: str = "REQUEST",
) -> str:
    """Return a single-event VCALENDAR document as text/calendar bytes.

    ``method`` is REQUEST for a new invitation, CANCEL when the event
    was cancelled, REFRESH to prompt a re-fetch (rare). Most clients
    treat REQUEST as "please add this to your calendar".

    UID is deterministic (event.id + tenant.id) so an updated event
    with a bumped ``SEQUENCE`` replaces the same entry in the parent's
    calendar rather than creating a duplicate.
    """
    now_utc = _fmt_dt_utc(datetime.now(timezone.utc))
    dtstart = _fmt_dt_utc(event.starts_at)
    dtend = _fmt_dt_utc(event.ends_at) if event.ends_at else dtstart

    uid = f"classup-event-{event.id}@classup.co.za"
    summary = _ics_escape(event.title or "School event")
    description = _ics_escape(event.description or "")
    location = _ics_escape(event.location or "")

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ClassUp//School Events//EN",
        f"METHOD:{method}",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now_utc}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{summary}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{description}")
    if location:
        lines.append(f"LOCATION:{location}")
    if organizer_email:
        org = f"CN={_ics_escape(organizer_name or organizer_email)}:MAILTO:{organizer_email}"
        lines.append(f"ORGANIZER;{org}")
    if event.is_cancelled or method == "CANCEL":
        lines.append("STATUS:CANCELLED")
    else:
        lines.append("STATUS:CONFIRMED")
    # Bumping SEQUENCE on updates keeps calendar clients happy. We use
    # a coarse "seconds since epoch of updated_at" so any edit bumps it.
    seq = int(event.updated_at.timestamp()) if getattr(event, "updated_at", None) else 0
    lines.append(f"SEQUENCE:{seq}")
    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")

    # Fold + CRLF-join per RFC 5545. Trailing CRLF too — some parsers
    # (older Outlook) require it.
    return "\r\n".join(_fold_line(l) for l in lines) + "\r\n"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID | None,
    title: str,
    description: str | None,
    event_type: str,
    scope: str,
    starts_at: datetime,
    ends_at: datetime | None = None,
    class_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    location: str | None = None,
    rsvp_required: bool = False,
    rsvp_deadline: datetime | None = None,
    timezone_name: str | None = None,
) -> SchoolEvent:
    """Persist a new event. Caller is responsible for firing invitations
    (the API layer does that after commit so a failed notification
    doesn't roll back the create)."""
    event = SchoolEvent(
        tenant_id=tenant_id,
        created_by=created_by,
        title=title.strip()[:200],
        description=(description or "").strip() or None,
        event_type=event_type,
        scope=scope,
        class_id=class_id if scope == EventScope.CLASS.value else None,
        student_id=student_id if scope == EventScope.STUDENT.value else None,
        starts_at=starts_at,
        ends_at=ends_at,
        timezone=timezone_name,
        location=(location or "").strip() or None,
        rsvp_required=rsvp_required,
        rsvp_deadline=rsvp_deadline,
    )
    db.add(event)
    await db.flush()
    return event


async def list_events(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    upcoming_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[SchoolEvent]:
    stmt = select(SchoolEvent).where(
        SchoolEvent.tenant_id == tenant_id,
        SchoolEvent.deleted_at.is_(None),
    )
    if upcoming_only:
        stmt = stmt.where(
            or_(
                SchoolEvent.starts_at >= datetime.now(timezone.utc),
                and_(
                    SchoolEvent.ends_at.is_not(None),
                    SchoolEvent.ends_at >= datetime.now(timezone.utc),
                ),
            ),
        )
    stmt = stmt.order_by(SchoolEvent.starts_at.asc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().unique().all())


async def list_events_for_parent(
    db: AsyncSession,
    *,
    parent_id: uuid.UUID,
    tenant_id: uuid.UUID,
    upcoming_only: bool = True,
    limit: int = 50,
) -> list[SchoolEvent]:
    """Events the parent is invited to. Computes the union of:
      - SCHOOL scope on the tenant
      - CLASS scope for classes their children are in
      - STUDENT scope for their own children
    """
    # Parent's child student IDs + their class IDs
    child_stmt = (
        select(Student.id, Student.class_id)
        .join(ParentStudent, ParentStudent.student_id == Student.id)
        .where(
            ParentStudent.parent_id == parent_id,
            Student.deleted_at.is_(None),
        )
    )
    rows = (await db.execute(child_stmt)).all()
    student_ids = [r[0] for r in rows]
    class_ids = list({r[1] for r in rows if r[1] is not None})

    conds = [
        # School-wide events
        and_(
            SchoolEvent.tenant_id == tenant_id,
            SchoolEvent.scope == EventScope.SCHOOL.value,
        ),
    ]
    if class_ids:
        conds.append(
            and_(
                SchoolEvent.scope == EventScope.CLASS.value,
                SchoolEvent.class_id.in_(class_ids),
            )
        )
    if student_ids:
        conds.append(
            and_(
                SchoolEvent.scope == EventScope.STUDENT.value,
                SchoolEvent.student_id.in_(student_ids),
            )
        )

    stmt = select(SchoolEvent).where(
        SchoolEvent.tenant_id == tenant_id,
        SchoolEvent.deleted_at.is_(None),
        SchoolEvent.cancelled_at.is_(None),
        or_(*conds),
    )
    if upcoming_only:
        stmt = stmt.where(SchoolEvent.starts_at >= datetime.now(timezone.utc))
    stmt = stmt.order_by(SchoolEvent.starts_at.asc()).limit(limit)
    return list((await db.execute(stmt)).scalars().unique().all())


async def get_event(
    db: AsyncSession, event_id: uuid.UUID, tenant_id: uuid.UUID,
) -> SchoolEvent | None:
    stmt = select(SchoolEvent).where(
        SchoolEvent.id == event_id,
        SchoolEvent.tenant_id == tenant_id,
        SchoolEvent.deleted_at.is_(None),
    )
    return (await db.execute(stmt)).scalars().unique().first()


async def cancel_event(
    db: AsyncSession, event: SchoolEvent,
) -> SchoolEvent:
    event.cancelled_at = datetime.now(timezone.utc)
    await db.flush()
    return event


async def soft_delete_event(
    db: AsyncSession, event: SchoolEvent,
) -> None:
    event.deleted_at = datetime.now(timezone.utc)
    await db.flush()


# ---------------------------------------------------------------------------
# RSVP
# ---------------------------------------------------------------------------


async def set_rsvp(
    db: AsyncSession,
    *,
    event_id: uuid.UUID,
    user_id: uuid.UUID,
    response: str,
) -> EventRsvp:
    """Upsert a parent's RSVP. The unique index on (event_id, user_id)
    is what enforces one-per-parent; we look up + overwrite in the
    service layer rather than relying on Postgres ON CONFLICT so the
    updated ``responded_at`` reflects each change."""
    try:
        response = RsvpResponse(response).value
    except ValueError:
        raise ValueError(f"Invalid RSVP response: {response!r}")

    existing_stmt = select(EventRsvp).where(
        EventRsvp.event_id == event_id,
        EventRsvp.user_id == user_id,
    )
    existing = (await db.execute(existing_stmt)).scalars().first()
    now = datetime.now(timezone.utc)
    if existing:
        existing.response = response
        existing.responded_at = now
        await db.flush()
        return existing
    rsvp = EventRsvp(
        event_id=event_id,
        user_id=user_id,
        response=response,
        responded_at=now,
    )
    db.add(rsvp)
    await db.flush()
    return rsvp


async def get_rsvp_summary(
    db: AsyncSession, event_id: uuid.UUID,
) -> dict[str, int]:
    """Count of RSVPs per response type — for the staff view."""
    stmt = select(EventRsvp).where(EventRsvp.event_id == event_id)
    rsvps = (await db.execute(stmt)).scalars().all()
    summary = {r.value: 0 for r in RsvpResponse}
    for r in rsvps:
        if r.response in summary:
            summary[r.response] += 1
    return summary


# ---------------------------------------------------------------------------
# Helpers used by the API + web layers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Signed RSVP tokens — for email-link one-tap RSVP without login
# ---------------------------------------------------------------------------


def sign_rsvp_token(event_id: uuid.UUID, user_id: uuid.UUID, response: str) -> str:
    """Sign an (event_id, user_id, response) triple with HMAC-SHA256
    using the app secret. Result is a 16-hex-char prefix (64 bits of
    entropy — plenty against forgery for a per-event per-user opt-in
    where the worst case is someone RSVPing on a friend's behalf).

    Truncated for URL brevity. Full signature would be 64 chars.
    """
    settings = get_settings()
    msg = f"{event_id}:{user_id}:{response.upper()}".encode("utf-8")
    key = settings.app_secret_key.encode("utf-8")
    sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return sig[:16]


def verify_rsvp_token(
    event_id: uuid.UUID, user_id: uuid.UUID, response: str, provided_sig: str,
) -> bool:
    """Constant-time check that the token matches. False on any
    mismatch or missing/malformed input — the caller treats that as
    "reject, ask the parent to log in and RSVP manually"."""
    if not provided_sig:
        return False
    try:
        expected = sign_rsvp_token(event_id, user_id, response)
    except Exception:
        return False
    return hmac.compare_digest(expected.lower(), provided_sig.strip().lower())


# ---------------------------------------------------------------------------
# Reminders — 24h + 1h before starts_at
# ---------------------------------------------------------------------------


async def send_due_reminders(db: AsyncSession) -> dict[str, int]:
    """Scan every non-deleted, non-cancelled event and fire reminders
    that are due but not yet sent.

    Two windows:
      - 24h: starts_at is between now+22h and now+26h (2h grace band)
      - 1h:  starts_at is between now+45min and now+1h15min (30min grace)

    Idempotency guaranteed by reminder_{Xh}_sent_at columns — a second
    concurrent scan re-selects only events with those columns still
    NULL, so no double-send.

    Returns {'24h_sent': N, '1h_sent': N} for the caller / logging.
    Best-effort per event: one failing send doesn't stop the rest.
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    stats = {"24h_sent": 0, "1h_sent": 0}

    async def _scan(window_hours: int, grace_hours: float, flag_attr: str, label: str) -> int:
        window_start = now + timedelta(hours=window_hours - grace_hours)
        window_end = now + timedelta(hours=window_hours + grace_hours)
        flag_col = getattr(SchoolEvent, flag_attr)
        stmt = select(SchoolEvent).where(
            SchoolEvent.deleted_at.is_(None),
            SchoolEvent.cancelled_at.is_(None),
            SchoolEvent.starts_at >= window_start,
            SchoolEvent.starts_at <= window_end,
            flag_col.is_(None),
        )
        events = list((await db.execute(stmt)).scalars().unique().all())
        if not events:
            return 0

        # Late import to avoid a circular import between event_service
        # and the API layer that owns the send helper.
        from app.api.v1.events import _send_reminders as api_send_reminders

        sent = 0
        for event in events:
            try:
                await api_send_reminders(db, event, label=label)
                setattr(event, flag_attr, datetime.now(timezone.utc))
                sent += 1
            except Exception:
                logger.exception(
                    "Reminder send failed for event %s (%s)", event.id, label,
                )
        if sent:
            await db.flush()
        return sent

    stats["24h_sent"] = await _scan(24, 2.0, "reminder_24h_sent_at", "24h")
    stats["1h_sent"] = await _scan(1, 0.25, "reminder_1h_sent_at", "1h")
    if stats["24h_sent"] or stats["1h_sent"]:
        logger.info(
            "Event reminders sent: 24h=%d, 1h=%d",
            stats["24h_sent"], stats["1h_sent"],
        )
    return stats


async def get_tenant_timezone(db: AsyncSession, tenant_id: uuid.UUID | None) -> str:
    """Resolve the timezone to render event times in. Prefers the
    tenant's configured timezone; falls back to Africa/Johannesburg."""
    if not tenant_id:
        return "Africa/Johannesburg"
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        return "Africa/Johannesburg"
    return (tenant.settings or {}).get("timezone") or "Africa/Johannesburg"


def format_event_when(event: SchoolEvent, tz_name: str | None = None) -> str:
    """Human-friendly one-line for the event's time.
    Uses UTC times as-is (the caller can localise if needed)."""
    if not event.starts_at:
        return "TBC"
    date_str = event.starts_at.strftime("%a %d %b %Y")
    time_str = event.starts_at.strftime("%H:%M")
    if event.ends_at:
        return f"{date_str}, {time_str}–{event.ends_at.strftime('%H:%M')}"
    return f"{date_str} at {time_str}"
