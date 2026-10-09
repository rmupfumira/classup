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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Announcement,
    AttendanceRecord,
    BillingInvoice,
    BillingInvoiceItem,
    DailyReport,
    EventRsvp,
    ParentStudent,
    SchoolEvent,
    Student,
    Tenant,
)
from app.models.attendance import AttendanceStatus
from app.services.jurisdiction_service import CURRENCY_SYMBOLS

logger = logging.getLogger(__name__)


# Urgency buckets drive the template's colour accents. Keep this
# vocabulary small (3 levels) so the UI stays legible — anything else
# becomes noise. Mapping:
#   critical → red (overdue invoices, EMERGENCY, late RSVP for today)
#   warning  → amber (due-soon invoices, URGENT, RSVP within 7 days)
#   info     → blue (passive callouts; currently unused in needs-attention)
_URGENCY_CRITICAL = "critical"
_URGENCY_WARNING = "warning"
_URGENCY_INFO = "info"


# ---------------------------------------------------------------------------
# Return shapes — kept as dataclasses so the template renders them with
# dot-notation; the web layer just .__dict__'s them if JSON is needed.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NeedsAttentionItem:
    """One actionable card on the dashboard.

    The ``urgency`` + ``icon`` fields let the template render the right
    colour accent + category badge without the template having to
    re-derive meaning from ``kind``. Keep the service as the source of
    presentation hints — the template only paints.
    """
    kind: str              # "invoice" | "event_rsvp" | "urgent_announcement" | "absence_today"
    title: str
    detail: str
    primary_label: str | None = None   # e.g. "Pay now"
    primary_url: str | None = None
    secondary_label: str | None = None
    secondary_url: str | None = None
    ordering_key: int = 0  # Lower = more urgent
    urgency: str = _URGENCY_INFO   # "critical" | "warning" | "info"
    icon: str = "bell"             # "invoice" | "announcement" | "rsvp" | "bell"
    amount_text: str | None = None  # Big-type amount for invoice cards
    badge_text: str | None = None   # Small coloured chip (e.g. "OVERDUE")


def _tenant_tz(tenant: Tenant | None) -> ZoneInfo:
    """Resolve the tenant's IANA zone, falling back to Africa/Harare.

    Africa/Harare is the house default (the first-market tenants are
    all GMT+2). Falls back further to UTC if the stored string isn't
    a recognised IANA name — safer than crashing a dashboard load.
    """
    name = "Africa/Harare"
    if tenant is not None:
        try:
            name = tenant.get_setting("timezone") or name
        except Exception:
            pass
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


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
    currency_symbol: str,
) -> list[NeedsAttentionItem]:
    """Unpaid invoices with due date within 7 days or already overdue.

    An invoice counts as needing attention when it's non-DRAFT, non-
    CANCELLED, has a positive balance, and either has no due date,
    is due within a week, or is already past due.

    Each card leads with the fee description (first line item) rather
    than the opaque invoice number so two consecutive invoices don't
    look identical in the feed. The invoice number drops into the
    detail line as a secondary reference.
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
    if not rows:
        return []

    # Batch-load the first line item per invoice — one query instead
    # of N. "First" = lowest id (invoice items don't have a sort column
    # beyond insertion order, which the UUIDv4 PK roughly tracks).
    inv_ids = [inv.id for inv in rows]
    items_stmt = (
        select(BillingInvoiceItem)
        .where(BillingInvoiceItem.invoice_id.in_(inv_ids))
        .order_by(BillingInvoiceItem.invoice_id, BillingInvoiceItem.id)
    )
    item_rows = (await db.execute(items_stmt)).scalars().all()
    first_item_by_invoice: dict[uuid.UUID, str] = {}
    for item in item_rows:
        if item.invoice_id not in first_item_by_invoice and item.description:
            first_item_by_invoice[item.invoice_id] = item.description

    out: list[NeedsAttentionItem] = []
    for inv in rows:
        description = (
            first_item_by_invoice.get(inv.id)
            or (inv.notes.strip() if inv.notes else None)
            or "School fees"
        )
        # Keep the card title to one strong line — fee description +
        # amount — so a parent eyeballing the feed knows WHAT it's for
        # and HOW MUCH at a glance.
        title = f"{description} — {currency_symbol}{inv.balance:,.2f}"

        overdue = inv.due_date is not None and inv.due_date < today
        if overdue:
            days = (today - inv.due_date).days
            detail = f"Invoice {inv.invoice_number} · overdue by {days} day{'s' if days != 1 else ''}"
            badge = "OVERDUE"
            urgency = _URGENCY_CRITICAL
            order = -10 - min(days, 30)  # longer overdue sorts higher
        elif inv.due_date is not None:
            days = (inv.due_date - today).days
            if days == 0:
                detail = f"Invoice {inv.invoice_number} · due today"
                badge = "DUE TODAY"
                urgency = _URGENCY_CRITICAL
            elif days == 1:
                detail = f"Invoice {inv.invoice_number} · due tomorrow"
                badge = "DUE SOON"
                urgency = _URGENCY_WARNING
            else:
                detail = (
                    f"Invoice {inv.invoice_number} · "
                    f"due {inv.due_date.strftime('%d %b')} ({days} days left)"
                )
                badge = "DUE SOON" if days <= 3 else None
                urgency = _URGENCY_WARNING if days <= 3 else _URGENCY_INFO
            order = days
        else:
            detail = f"Invoice {inv.invoice_number}"
            badge = None
            urgency = _URGENCY_INFO
            order = 100

        out.append(NeedsAttentionItem(
            kind="invoice",
            title=title,
            detail=detail,
            # No "Pay now" button — ClassUp doesn't collect parent
            # payments. Fees are settled via the school's own banking
            # details on the invoice (EFT / cash / however that school
            # already handles it). The ONLY in-app payment flow is
            # tenants paying their ClassUp subscription. CTA stays as
            # "View invoice" everywhere — the parent opens the invoice
            # to see banking details and reference number.
            primary_label="View invoice",
            primary_url=f"/billing/invoices/{inv.id}",
            ordering_key=order,
            urgency=urgency,
            icon="invoice",
            amount_text=f"{currency_symbol}{inv.balance:,.2f}",
            badge_text=badge,
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
            title=e.title,
            detail=detail,
            primary_label="RSVP",
            primary_url=f"/events#event-{e.id}",
            ordering_key=days_to_start - 5,  # closer events sort higher
            urgency=_URGENCY_CRITICAL if days_to_start <= 1 else _URGENCY_WARNING,
            icon="rsvp",
            badge_text="RSVP TODAY" if days_to_start == 0 else "RSVP NEEDED",
        ))
    return out


async def _urgent_announcements(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    student_class_ids: list[uuid.UUID],
    tz: ZoneInfo,
) -> list[NeedsAttentionItem]:
    """URGENT / EMERGENCY announcements from the last 14 days.

    Timestamps are converted to the tenant's timezone — the previous
    rendering showed raw UTC which confused parents whose school is
    in a different offset.
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
        # Announcement.created_at from Postgres is naive-UTC via
        # SQLAlchemy — anchor it to UTC before converting so DST on
        # the tenant's zone resolves correctly.
        when = ""
        if a.created_at:
            created_utc = a.created_at
            if created_utc.tzinfo is None:
                created_utc = created_utc.replace(tzinfo=timezone.utc)
            when = created_utc.astimezone(tz).strftime("%d %b · %H:%M")
        urgency = (
            _URGENCY_CRITICAL if a.severity == "EMERGENCY"
            else _URGENCY_WARNING
        )
        badge = "EMERGENCY" if a.severity == "EMERGENCY" else "URGENT"
        out.append(NeedsAttentionItem(
            kind="urgent_announcement",
            title=a.title,
            detail=when,
            primary_label="Read",
            primary_url=f"/announcements#a-{a.id}",
            ordering_key=-15 if a.severity == "EMERGENCY" else -5,
            urgency=urgency,
            icon="announcement",
            badge_text=badge,
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

    # Resolve tenant presentation hints up-front so every aggregator
    # can format consistently (currency symbol on invoices, local tz
    # on timestamps). Fail soft — we never want a stale setting to
    # kill the dashboard.
    tenant = None
    currency_code = "USD"
    currency_symbol = "$"
    try:
        tenant = await db.get(Tenant, tenant_id)
        if tenant:
            currency_code = tenant.get_setting("billing_currency", "USD") or "USD"
            currency_symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code)
    except Exception:
        logger.exception("tenant lookup failed for parent dashboard (parent %s)", parent_id)
    tz = _tenant_tz(tenant)

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
        needs.extend(await _unpaid_invoices(db, tenant_id, child_ids, today, currency_symbol))
    except Exception:
        logger.exception("needs_attention: unpaid invoices failed for parent %s", parent_id)
    try:
        needs.extend(await _pending_rsvps(db, tenant_id, parent_id, child_ids, today))
    except Exception:
        logger.exception("needs_attention: pending RSVPs failed for parent %s", parent_id)
    try:
        needs.extend(await _urgent_announcements(db, tenant_id, class_ids, tz))
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

    # Tenant name + currency for presentation (already loaded above).
    if tenant:
        dashboard.tenant_name = tenant.name
        dashboard.billing_currency = currency_code

    return dashboard
