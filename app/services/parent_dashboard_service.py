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
    # Parent Portal iter 1 (P-DASH-01 / P-MULTI-03): which child(ren)
    # the card concerns ("Rudo", "Rudo · Tatenda") and a secondary line
    # (invoice items, periods). Both are presentation-only.
    child_label: str | None = None
    secondary: str | None = None


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


@dataclass(frozen=True)
class DayDot:
    """One school day in the "this week" strip (P-DASH-04)."""
    label: str      # "Mon"
    day: date
    state: str      # present | late | absent | excused | unmarked | upcoming


@dataclass(frozen=True)
class TodayStatus:
    """The selected child's attendance for today (P-DASH-04)."""
    state: str      # present | late | absent | excused | unmarked | no_school
    label: str      # "Present", "Late · 08:12", "Not marked yet", ...


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
    # Parent Portal iter 1 additions (all read-only / presentation).
    today_status: TodayStatus | None = None
    week_dots: list[DayDot] = field(default_factory=list)
    family_outstanding: Decimal = Decimal("0")
    family_outstanding_text: str | None = None
    children_with_balance: int = 0


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
    # Canonical parent-portal order (same key as app/web/_parent_context).
    rows = sorted(rows, key=lambda s: (
        (s.first_name or "").lower(), (s.last_name or "").lower(), str(s.id),
    ))
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
    child_names: dict[uuid.UUID, str] | None = None,
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
        .limit(50)
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return []
    names = child_names or {}

    # Batch-load the line items — one query instead of N. Insertion
    # order (the PK) is the only ordering invoice items have.
    inv_ids = [inv.id for inv in rows]
    items_stmt = (
        select(BillingInvoiceItem)
        .where(BillingInvoiceItem.invoice_id.in_(inv_ids))
        .order_by(BillingInvoiceItem.invoice_id, BillingInvoiceItem.id)
    )
    item_rows = (await db.execute(items_stmt)).scalars().all()
    items_by_invoice: dict[uuid.UUID, list[str]] = {}
    for item in item_rows:
        if item.description:
            items_by_invoice.setdefault(item.invoice_id, []).append(item.description)

    def _money(v) -> str:
        return f"{currency_symbol}{v:,.2f}"

    # P-DASH-01: when a child has more than one overdue invoice, show ONE
    # collapsed card for that child instead of a stack of red cards.
    overdue_by_child: dict[uuid.UUID, list[BillingInvoice]] = {}
    for inv in rows:
        if inv.due_date is not None and inv.due_date < today:
            overdue_by_child.setdefault(inv.student_id, []).append(inv)
    collapsed_children = {sid for sid, invs in overdue_by_child.items() if len(invs) > 1}

    out: list[NeedsAttentionItem] = []
    for sid in collapsed_children:
        invs = overdue_by_child[sid]
        total = sum((i.balance for i in invs), Decimal("0"))
        oldest = min(i.due_date for i in invs)
        days = (today - oldest).days
        periods = [_period_label(i) for i in invs]
        out.append(NeedsAttentionItem(
            kind="invoice",
            title=f"{len(invs)} overdue invoices · {_money(total)} outstanding",
            detail=f"Oldest due {oldest.strftime('%d %b %Y')} · overdue by {days} day{'s' if days != 1 else ''}",
            primary_label="See fees",
            primary_url=f"/billing/statement?child_id={sid}",
            ordering_key=-10 - min(days, 30),
            urgency=_URGENCY_CRITICAL,
            icon="invoice",
            amount_text=_money(total),
            badge_text="OVERDUE",
            child_label=names.get(sid),
            secondary=" · ".join(dict.fromkeys(periods)),
        ))

    for inv in rows:
        if inv.student_id in collapsed_children and inv.due_date is not None and inv.due_date < today:
            continue
        item_names = items_by_invoice.get(inv.id) or []
        items_text = ", ".join(dict.fromkeys(item_names)) or (
            inv.notes.strip() if inv.notes else None
        )
        # P-DASH-03: lead with the period (what it's for) + the
        # outstanding balance (how much). Items drop to a secondary line.
        title = f"{_period_label(inv)} fees · {_money(inv.balance)} outstanding"

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
            amount_text=_money(inv.balance),
            badge_text=badge,
            child_label=names.get(inv.student_id),
            secondary=items_text,
        ))
    return out


def _period_label(inv: BillingInvoice) -> str:
    """"Aug 2026", "Jul–Aug 2026", "Dec 2025–Jan 2026"; falls back to
    the due month, then to the invoice number."""
    start, end = inv.billing_period_start, inv.billing_period_end
    if start and end:
        if (start.year, start.month) == (end.year, end.month):
            return start.strftime("%b %Y")
        if start.year == end.year:
            return f"{start.strftime('%b')}–{end.strftime('%b %Y')}"
        return f"{start.strftime('%b %Y')}–{end.strftime('%b %Y')}"
    if start:
        return start.strftime("%b %Y")
    if inv.due_date:
        return inv.due_date.strftime("%b %Y")
    return inv.invoice_number or "School"


async def _family_outstanding(
    db: AsyncSession, tenant_id: uuid.UUID, student_ids: list[uuid.UUID],
) -> tuple[Decimal, int]:
    """Sum of open balances across the family + how many children owe.

    Read-only aggregate of the stored ``balance`` column (no new
    financial calculation) over the same invoice set the cards use.
    """
    if not student_ids:
        return Decimal("0"), 0
    rows = (await db.execute(
        select(BillingInvoice.student_id, func.sum(BillingInvoice.balance))
        .where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.student_id.in_(student_ids),
            BillingInvoice.deleted_at.is_(None),
            BillingInvoice.status.not_in(("DRAFT", "CANCELLED")),
            BillingInvoice.balance > 0,
        )
        .group_by(BillingInvoice.student_id)
    )).all()
    total = sum((Decimal(str(r[1] or 0)) for r in rows), Decimal("0"))
    return total, sum(1 for r in rows if (r[1] or 0) > 0)


async def _pending_rsvps(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    parent_id: uuid.UUID,
    student_ids: list[uuid.UUID],
    today: date,
    class_children: dict[uuid.UUID, list[str]] | None = None,
    child_names: dict[uuid.UUID, str] | None = None,
) -> list[NeedsAttentionItem]:
    """Events this parent is invited to with no RSVP yet, where the
    deadline is within 7 days or the event itself is within 7 days."""
    if not student_ids:
        return []
    class_children = class_children or {}
    child_names = child_names or {}

    # Only events whose audience includes this parent (P-MULTI-03):
    # SCHOOL scope, CLASS scope for one of the children's classes, or
    # STUDENT scope for one of the children. Mirrors
    # event_service.resolve_audience.
    audience = [SchoolEvent.scope == "SCHOOL",
                and_(SchoolEvent.scope == "STUDENT", SchoolEvent.student_id.in_(student_ids))]
    if class_children:
        audience.append(and_(
            SchoolEvent.scope == "CLASS",
            SchoolEvent.class_id.in_(list(class_children.keys())),
        ))
    deadline_cutoff = datetime.now(timezone.utc) + timedelta(days=7)
    stmt = (
        select(SchoolEvent)
        .where(
            SchoolEvent.tenant_id == tenant_id,
            SchoolEvent.deleted_at.is_(None),
            SchoolEvent.cancelled_at.is_(None),
            SchoolEvent.rsvp_required.is_(True),
            SchoolEvent.starts_at >= datetime.now(timezone.utc),
            or_(*audience),
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
            child_label=(
                child_names.get(e.student_id) if e.scope == "STUDENT"
                else " · ".join(class_children.get(e.class_id, [])) or None
                if e.scope == "CLASS" else "Whole school"
            ),
        ))
    return out


async def _urgent_announcements(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    student_class_ids: list[uuid.UUID],
    tz: ZoneInfo,
    class_children: dict[uuid.UUID, list[str]] | None = None,
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
            child_label=(
                " · ".join((class_children or {}).get(a.class_id, [])) or None
                if a.class_id else "Whole school"
            ),
        ))
    return out


_DAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _fmt_time(value: datetime | None, tz: ZoneInfo | None) -> str | None:
    if value is None:
        return None
    try:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if tz is not None:
            value = value.astimezone(tz)
        return value.strftime("%H:%M")
    except Exception:
        return None


async def _attendance_week(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    student_id: uuid.UUID,
    today: date,
    tz: ZoneInfo | None = None,
) -> tuple[ThisWeekItem | None, TodayStatus, list[DayDot]]:
    """This week's records for one child, in ONE read-only query.

    Returns the "This week" summary card, today's status and the
    Mon–Fri day dots (P-DASH-04). Late is reported as late (never as a
    tick); the summary counts late days as "in school", matching the
    attendance-rate definition used on /attendance/student/{id}.
    """
    monday = today - timedelta(days=today.weekday())
    week_end = monday + timedelta(days=6)
    rows = (await db.execute(
        select(AttendanceRecord.date, AttendanceRecord.status, AttendanceRecord.check_in_time)
        .where(
            AttendanceRecord.tenant_id == tenant_id,
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.date >= monday,
            AttendanceRecord.date <= week_end,
        )
    )).all()
    by_day = {r[0]: (str(r[1]).upper(), r[2]) for r in rows}

    # Today
    rec = by_day.get(today)
    if rec is not None:
        status, check_in = rec
        if status == AttendanceStatus.LATE.value:
            t = _fmt_time(check_in, tz)
            today_status = TodayStatus("late", f"Late · arrived {t}" if t else "Late")
        elif status == AttendanceStatus.PRESENT.value:
            t = _fmt_time(check_in, tz)
            today_status = TodayStatus("present", f"Present · in at {t}" if t else "Present")
        elif status == AttendanceStatus.ABSENT.value:
            today_status = TodayStatus("absent", "Absent")
        elif status == "EXEAT":
            today_status = TodayStatus("excused", "Away on exeat")
        else:
            today_status = TodayStatus("excused", "Excused")
    elif today.weekday() >= 5:
        today_status = TodayStatus("no_school", "No school today")
    else:
        today_status = TodayStatus("unmarked", "Not marked yet")

    # Day dots — school week Mon–Fri, plus a weekend day only if marked.
    dots: list[DayDot] = []
    for i in range(7):
        d = monday + timedelta(days=i)
        rec = by_day.get(d)
        if i >= 5 and rec is None:
            continue
        if rec is not None:
            state = {
                AttendanceStatus.PRESENT.value: "present",
                AttendanceStatus.LATE.value: "late",
                AttendanceStatus.ABSENT.value: "absent",
            }.get(rec[0], "excused")
        else:
            state = "upcoming" if d > today else "unmarked"
        dots.append(DayDot(label=_DAY_LABELS[i], day=d, state=state))

    statuses = [v[0] for d, v in by_day.items() if d <= today]
    present = statuses.count(AttendanceStatus.PRESENT.value)
    late = statuses.count(AttendanceStatus.LATE.value)
    absent = statuses.count(AttendanceStatus.ABSENT.value)
    excused = statuses.count(AttendanceStatus.EXCUSED.value)
    total_marked = present + late + absent + excused
    summary = None
    if total_marked:
        in_school = present + late
        title = f"Present {in_school} of {total_marked} day{'s' if total_marked != 1 else ''} this week"
        parts = []
        if late:
            parts.append(f"{late} late")
        if absent:
            parts.append(f"{absent} absent")
        if excused:
            parts.append(f"{excused} excused")
        summary = ThisWeekItem(
            kind="attendance",
            title=title,
            detail=" · ".join(parts) if parts else "On time every day so far",
            url=f"/attendance/student/{student_id}",
        )
    return summary, today_status, dots


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
        title="New report published",
        detail=f"Published {finalised} · Tap to read or download",
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
    child_names = {c.id: c.first_name for c in children}
    # Class ids straight from the Student rows (ChildCard only carries
    # the class name). class_children maps class → child first names
    # for the per-card child labels (P-MULTI-03).
    class_children: dict[uuid.UUID, list[str]] = {}
    try:
        class_id_rows = (await db.execute(
            select(Student.id, Student.class_id)
            .where(
                Student.id.in_(child_ids),
                Student.class_id.is_not(None),
            )
        )).all() if child_ids else []
        for sid_, cid_ in class_id_rows:
            if cid_:
                class_children.setdefault(cid_, []).append(child_names.get(sid_, ""))
    except Exception:
        class_children = {}
    class_ids = list(class_children.keys())

    # Needs attention — collect from each source, sort by ordering_key.
    needs: list[NeedsAttentionItem] = []
    try:
        needs.extend(await _unpaid_invoices(
            db, tenant_id, child_ids, today, currency_symbol, child_names,
        ))
    except Exception:
        logger.exception("needs_attention: unpaid invoices failed for parent %s", parent_id)
    try:
        needs.extend(await _pending_rsvps(
            db, tenant_id, parent_id, child_ids, today, class_children, child_names,
        ))
    except Exception:
        logger.exception("needs_attention: pending RSVPs failed for parent %s", parent_id)
    try:
        needs.extend(await _urgent_announcements(db, tenant_id, class_ids, tz, class_children))
    except Exception:
        logger.exception("needs_attention: urgent announcements failed for parent %s", parent_id)
    needs.sort(key=lambda i: i.ordering_key)
    dashboard.needs_attention = needs

    # Family total (P-DASH-01) — shown by the template for 2+ children.
    try:
        total, owing = await _family_outstanding(db, tenant_id, child_ids)
        dashboard.family_outstanding = total
        dashboard.children_with_balance = owing
        if total > 0:
            dashboard.family_outstanding_text = f"{currency_symbol}{total:,.2f}"
    except Exception:
        logger.exception("family outstanding failed for parent %s", parent_id)

    # This week — scoped to the selected child.
    this_week: list[ThisWeekItem] = []
    if dashboard.selected_child:
        sid = dashboard.selected_child.id
        try:
            att, today_status, dots = await _attendance_week(db, tenant_id, sid, today, tz)
            dashboard.today_status = today_status
            dashboard.week_dots = dots
            if att:
                this_week.append(att)
        except Exception:
            logger.exception("this_week: attendance week failed for parent %s", parent_id)
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
