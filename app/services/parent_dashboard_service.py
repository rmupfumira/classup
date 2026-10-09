"""Parent dashboard aggregators — one place for all the queries that
power the three-section dashboard (Needs attention / This week / Quick
actions). Keeps web/dashboard.py thin and gives templates a stable,
documented shape to render.

2026-10-09 onboarding redesign. Replaces the per-child
`attendance_history` + `recent_reports` list the old parent dashboard
dumped into a template — instead, this module ranks items by
actionability so the parent sees what matters first.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Announcement,
    AttendanceRecord,
    BillingInvoice,
    DailyReport,
    EventRsvp,
    ParentStudent,
    SchoolEvent,
    Student,
    Tenant,
)
from app.models.attendance import AttendanceStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return shapes — kept as dataclasses so the template renders them with
# dot-notation; the web layer just .__dict__'s them if JSON is needed.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NeedsAttentionItem:
    """One actionable card on the dashboard."""
    kind: str              # "invoice" | "event_rsvp" | "urgent_announcement" | "absence_today"
    title: str
    detail: str
    primary_label: str | None = None   # e.g. "Pay now"
    primary_url: str | None = None
    secondary_label: str | None = None
    secondary_url: str | None = None
    ordering_key: int = 0  # Lower = more urgent


@dataclass(frozen=True)
class ThisWeekItem:
    """One passive-info card."""
    kind: str              # "attendance" | "photos" | "report"
    title: str
    detail: str
    url: str | None = None


@dataclass(frozen=True)
class ChildCard:
    """One child shown in the multi-child selector."""
    id: uuid.UUID
    first_name: str
    last_name: str
    class_name: str | None


@dataclass
class ParentDashboard:
    """Everything the parent dashboard template needs."""
    children: list[ChildCard] = field(default_factory=list)
    selected_child: ChildCard | None = None
    needs_attention: list[NeedsAttentionItem] = field(default_factory=list)
    this_week: list[ThisWeekItem] = field(default_factory=list)
    unread_messages: int = 0
    billing_currency: str = "USD"
    tenant_name: str = ""


# ---------------------------------------------------------------------------
# Aggregators — one per section. Each is tolerant to inner failures
# (we log + skip rather than crash the whole dashboard).
# ---------------------------------------------------------------------------

async def _load_children(
    db: AsyncSession, parent_id: uuid.UUID, tenant_id: uuid.UUID,
) -> list[ChildCard]:
    """All non-deleted children linked to this parent at this tenant."""
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Student)
        .join(ParentStudent, ParentStudent.student_id == Student.id)
        .where(
            ParentStudent.parent_id == parent_id,
            Student.tenant_id == tenant_id,
            Student.deleted_at.is_(None),
        )
        .options(selectinload(Student.school_class))
        .order_by(Student.first_name, Student.last_name)
    )
    rows = (await db.execute(stmt)).scalars().unique().all()
    return [
        ChildCard(
            id=s.id,
            first_name=s.first_name,
            last_name=s.last_name,
            class_name=s.school_class.name if s.school_class else None,
        )
        for s in rows
    ]


async def _unpaid_invoices(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    student_ids: list[uuid.UUID],
    today: date,
) -> list[NeedsAttentionItem]:
    """Unpaid invoices with due date within 7 days or already overdue.

    An invoice counts as needing attention when it's non-DRAFT, non-
    CANCELLED, has a positive balance, and either has no due date,
    is due within a week, or is already past due.
    """
    if not student_ids:
        return []
    cutoff = today + timedelta(days=7)
    stmt = (
        select(BillingInvoice)
        .where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.student_id.in_(student_ids),
            BillingInvoice.deleted_at.is_(None),
            BillingInvoice.status.not_in(("DRAFT", "CANCELLED")),
            BillingInvoice.balance > 0,
            or_(
                BillingInvoice.due_date.is_(None),
                BillingInvoice.due_date <= cutoff,
            ),
        )
        .order_by(BillingInvoice.due_date.asc().nulls_last())
        .limit(5)
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: list[NeedsAttentionItem] = []
    for inv in rows:
        overdue = inv.due_date is not None and inv.due_date < today
        if overdue:
            detail = (
                f"Invoice {inv.invoice_number} · "
                f"overdue by {(today - inv.due_date).days} days"
            )
            order = -10  # overdue sorts first
        elif inv.due_date is not None:
            detail = (
                f"Invoice {inv.invoice_number} · "
                f"due {inv.due_date.strftime('%d %b')} "
                f"({(inv.due_date - today).days} day"
                f"{'s' if (inv.due_date - today).days != 1 else ''} left)"
            )
            order = (inv.due_date - today).days
        else:
            detail = f"Invoice {inv.invoice_number}"
            order = 100

        out.append(NeedsAttentionItem(
            kind="invoice",
            title=f"💰 Amount outstanding: {inv.balance:.2f}",
            detail=detail,
            primary_label="View invoice",
            primary_url=f"/billing/invoices/{inv.id}",
            ordering_key=order,
        ))
    return out


async def _pending_rsvps(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    parent_id: uuid.UUID,
    student_ids: list[uuid.UUID],
    today: date,
) -> list[NeedsAttentionItem]:
    """Events this parent is invited to with no RSVP yet, where the
    deadline is within 7 days or the event itself is within 7 days."""
    from sqlalchemy.orm import selectinload

    if not student_ids:
        return []

    # Collect candidate events: SCHOOL-scope on this tenant, or
    # STUDENT-scope for any of this parent's children. (CLASS-scope
    # would need class_ids too — skipped here since the signal is
    # "needs me to RSVP" and class-wide events usually RSVP by class
    # anyway. Future enhancement.)
    deadline_cutoff = datetime.now(timezone.utc) + timedelta(days=7)
    stmt = (
        select(SchoolEvent)
        .where(
            SchoolEvent.tenant_id == tenant_id,
            SchoolEvent.deleted_at.is_(None),
            SchoolEvent.cancelled_at.is_(None),
            SchoolEvent.rsvp_required.is_(True),
            SchoolEvent.starts_at >= datetime.now(timezone.utc),
            or_(
                SchoolEvent.rsvp_deadline.is_(None),
                SchoolEvent.rsvp_deadline <= deadline_cutoff,
            ),
        )
        .order_by(SchoolEvent.starts_at.asc())
        .limit(10)
    )
    events = (await db.execute(stmt)).scalars().all()

    # Which of those does this parent still need to respond to?
    event_ids = [e.id for e in events]
    if not event_ids:
        return []
    rsvp_rows = (await db.execute(
        select(EventRsvp.event_id)
        .where(
            EventRsvp.event_id.in_(event_ids),
            EventRsvp.user_id == parent_id,
        )
    )).all()
    answered = {row[0] for row in rsvp_rows}

    out: list[NeedsAttentionItem] = []
    for e in events:
        if e.id in answered:
            continue
        when = e.starts_at.strftime("%a %d %b, %H:%M") if e.starts_at else ""
        detail = e.location or when or "RSVP needed"
        if e.location and when:
            detail = f"{when} · {e.location}"
        days_to_start = max(
            (e.starts_at.date() - today).days if e.starts_at else 7,
            0,
        )
        out.append(NeedsAttentionItem(
            kind="event_rsvp",
            title=f"📅 RSVP: {e.title}",
            detail=detail,
            primary_label="View event",
            primary_url=f"/events#event-{e.id}",
            ordering_key=days_to_start - 5,  # closer events sort higher
        ))
    return out


async def _urgent_announcements(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    student_class_ids: list[uuid.UUID],
) -> list[NeedsAttentionItem]:
    """URGENT / EMERGENCY announcements from the last 14 days.

    (Everything shorter-term shows under ``this_week`` instead — this
    section is specifically for things that need action or awareness
    now.)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    stmt = (
        select(Announcement)
        .where(
            Announcement.tenant_id == tenant_id,
            Announcement.deleted_at.is_(None),
            Announcement.severity.in_(("URGENT", "EMERGENCY")),
            Announcement.created_at >= cutoff,
        )
    )
    if student_class_ids:
        stmt = stmt.where(
            or_(
                Announcement.class_id.is_(None),
                Announcement.class_id.in_(student_class_ids),
            )
        )
    else:
        stmt = stmt.where(Announcement.class_id.is_(None))
    stmt = stmt.order_by(Announcement.created_at.desc()).limit(5)

    rows = (await db.execute(stmt)).scalars().all()
    out: list[NeedsAttentionItem] = []
    for a in rows:
        icon = "🚨" if a.severity == "EMERGENCY" else "⚠️"
        when = a.created_at.strftime("%d %b, %H:%M") if a.created_at else ""
        out.append(NeedsAttentionItem(
            kind="urgent_announcement",
            title=f"{icon} {a.title}",
            detail=f"{a.severity} · {when}",
            primary_label="Read",
            primary_url=f"/announcements#a-{a.id}",
            ordering_key=-5,
        ))
    return out


async def _attendance_rollup_this_week(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    student_id: uuid.UUID,
    today: date,
) -> ThisWeekItem | None:
    """Count attendance statuses from the Monday of this week to today."""
    monday = today - timedelta(days=today.weekday())
    stmt = (
        select(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .where(
            AttendanceRecord.tenant_id == tenant_id,
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.date >= monday,
            AttendanceRecord.date <= today,
        )
        .group_by(AttendanceRecord.status)
    )
    rows = (await db.execute(stmt)).all()
    counts = {row[0]: int(row[1]) for row in rows}
    present = counts.get(AttendanceStatus.PRESENT.value, 0)
    late = counts.get(AttendanceStatus.LATE.value, 0)
    absent = counts.get(AttendanceStatus.ABSENT.value, 0)
    excused = counts.get(AttendanceStatus.EXCUSED.value, 0)
    total_marked = present + late + absent + excused
    if total_marked == 0:
        return None

    present_total = present + late  # late still counts as "in school"
    title = f"✓ Attendance: {present_total} of {total_marked} days present"
    parts = []
    if absent:
        parts.append(f"{absent} absent")
    if excused:
        parts.append(f"{excused} excused")
    if late:
        parts.append(f"{late} late")
    detail = " · ".join(parts) if parts else "All present this week"
    return ThisWeekItem(
        kind="attendance",
        title=title,
        detail=detail,
        url=f"/attendance?student_id={student_id}",
    )


async def _recent_report(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    student_id: uuid.UUID,
    today: date,
) -> ThisWeekItem | None:
    """Latest finalised report in the last 7 days."""
    cutoff = today - timedelta(days=7)
    stmt = (
        select(DailyReport)
        .where(
            DailyReport.tenant_id == tenant_id,
            DailyReport.student_id == student_id,
            DailyReport.deleted_at.is_(None),
            DailyReport.status == "FINALIZED",
            DailyReport.finalized_at.is_not(None),
            DailyReport.finalized_at >= datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc),
        )
        .order_by(DailyReport.finalized_at.desc())
        .limit(1)
    )
    report = (await db.execute(stmt)).scalar_one_or_none()
    if report is None:
        return None
    finalised = report.finalized_at.strftime("%d %b") if report.finalized_at else "recently"
    return ThisWeekItem(
        kind="report",
        title="📄 New report ready",
        detail=f"Finalised {finalised} · PDF available",
        url=f"/reports/{report.id}",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def build_parent_dashboard(
    db: AsyncSession,
    parent_id: uuid.UUID,
    tenant_id: uuid.UUID,
    selected_child_id: uuid.UUID | None = None,
) -> ParentDashboard:
    """Assemble the three-section parent dashboard.

    ``selected_child_id`` scopes attendance/reports to one child when
    the parent has multiple. ``None`` → the first child alphabetically
    (matches the children list order).

    Every aggregator is wrapped in try/except so one slow or broken
    inner call can't take the whole page down.
    """
    today = date.today()
    dashboard = ParentDashboard()

    # Children + selection
    try:
        children = await _load_children(db, parent_id, tenant_id)
    except Exception:
        logger.exception("Failed to load children for parent %s", parent_id)
        children = []
    dashboard.children = children

    if children:
        if selected_child_id:
            dashboard.selected_child = next(
                (c for c in children if c.id == selected_child_id), children[0],
            )
        else:
            dashboard.selected_child = children[0]
    child_ids = [c.id for c in children]
    class_ids = [c.id for c in children if c.class_name]  # placeholder — we actually need Student.class_id
    # Re-fetch class ids directly from Student rows for accuracy.
    try:
        class_id_rows = (await db.execute(
            select(Student.class_id)
            .where(
                Student.id.in_(child_ids),
                Student.class_id.is_not(None),
            )
        )).all()
        class_ids = [row[0] for row in class_id_rows if row[0]]
    except Exception:
        class_ids = []

    # Needs attention — collect from each source, sort by ordering_key.
    needs: list[NeedsAttentionItem] = []
    try:
        needs.extend(await _unpaid_invoices(db, tenant_id, child_ids, today))
    except Exception:
        logger.exception("needs_attention: unpaid invoices failed for parent %s", parent_id)
    try:
        needs.extend(await _pending_rsvps(db, tenant_id, parent_id, child_ids, today))
    except Exception:
        logger.exception("needs_attention: pending RSVPs failed for parent %s", parent_id)
    try:
        needs.extend(await _urgent_announcements(db, tenant_id, class_ids))
    except Exception:
        logger.exception("needs_attention: urgent announcements failed for parent %s", parent_id)
    needs.sort(key=lambda i: i.ordering_key)
    dashboard.needs_attention = needs

    # This week — scoped to the selected child.
    this_week: list[ThisWeekItem] = []
    if dashboard.selected_child:
        sid = dashboard.selected_child.id
        try:
            att = await _attendance_rollup_this_week(db, tenant_id, sid, today)
            if att:
                this_week.append(att)
        except Exception:
            logger.exception("this_week: attendance rollup failed for parent %s", parent_id)
        try:
            rpt = await _recent_report(db, tenant_id, sid, today)
            if rpt:
                this_week.append(rpt)
        except Exception:
            logger.exception("this_week: recent report failed for parent %s", parent_id)
    dashboard.this_week = this_week

    # Unread messages (small side query — cheap).
    try:
        from app.services.message_service import get_message_service
        dashboard.unread_messages = await get_message_service().get_unread_count(db)
    except Exception:
        pass

    # Tenant name + currency for presentation.
    try:
        tenant = await db.get(Tenant, tenant_id)
        if tenant:
            dashboard.tenant_name = tenant.name
            dashboard.billing_currency = tenant.get_setting("billing_currency", "USD")
    except Exception:
        pass

    return dashboard
