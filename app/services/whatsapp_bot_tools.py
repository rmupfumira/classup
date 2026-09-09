"""Read-only tool functions the WhatsApp bot calls to answer parent questions.

Two important properties every tool in this module holds to:

1. **Tenant-scoped by user.** Every tool takes ``parent_id`` (the sender's
   ``users.id``) and relies on ``tenant_context._tenant_id`` being set by
   the caller. Callers must set it from ``user.tenant_id`` before invoking
   any tool — the menu-bot dispatcher does this.

2. **Parent-owns-child enforced at the tool boundary.** Any tool that
   takes a ``child_id`` calls ``_verify_parent_owns_child`` first and
   raises ``ForbiddenException`` on a miss. This is the tool layer's
   authorization contract — it does not trust the caller to have checked.
   Phase 2C's AI mode will get the same guarantee for free.

Response types are small dataclasses. The menu bot / AI mode formats them
into WhatsApp text; keeping the format decision at the caller means one
tool can serve both menu buttons and free-form AI answers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import ForbiddenException
from app.models import (
    Announcement,
    DailyReport,
    ParentStudent,
    SchoolClass,
    Student,
    User,
)


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChildSummary:
    """A parent's child, plus enough context to display in a menu row."""
    id: uuid.UUID
    first_name: str
    last_name: str
    class_name: str | None
    teacher_name: str | None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass(frozen=True)
class BalanceInfo:
    child_name: str
    total_outstanding: Decimal
    currency: str
    unpaid_invoices: list[dict]
    # unpaid_invoices items: {"number": str, "due_date": date | None,
    #                          "balance": Decimal}


@dataclass(frozen=True)
class AttendanceDay:
    date: date
    status: str
    check_in_time: datetime | None
    notes: str | None


@dataclass(frozen=True)
class AttendanceSummary:
    child_name: str
    days_shown: int
    records: list[AttendanceDay]


@dataclass(frozen=True)
class ReportSummary:
    """A finalized report ready for the parent to view."""
    child_name: str
    report_date: date
    finalized_at: datetime | None
    url: str  # absolute URL — safe to send directly


@dataclass(frozen=True)
class TeacherInfo:
    child_name: str
    teacher_name: str | None
    class_name: str | None


@dataclass(frozen=True)
class AnnouncementItem:
    title: str
    body: str
    is_pinned: bool
    created_at: datetime
    class_name: str | None  # None = school-wide


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _resolve_tenant_id(
    db: AsyncSession, parent_id: uuid.UUID
) -> uuid.UUID:
    """Return the tenant_id to scope tool queries by.

    Fast path: the request-scoped tenant context (set by the bot
    dispatchers before calling any tool) — matches every other
    service in the app.

    Fallback: if the context isn't set (e.g. an entry point I missed
    when wiring a new mode), derive it from the parent's own user row.
    Warns loudly so we notice + fix the real caller, but keeps the
    bot working instead of confidently telling parents they have no
    children linked (which is the failure mode when a query is scoped
    to a null tenant).

    Raises TenantContextError only when both paths fail — the parent
    doesn't exist or has no tenant.
    """
    from app.utils.tenant_context import get_tenant_id_or_none

    tid = get_tenant_id_or_none()
    if tid is not None:
        return tid

    from app.exceptions import TenantContextError
    user = await db.get(User, parent_id)
    if user is None or user.tenant_id is None:
        raise TenantContextError(
            "Cannot resolve tenant — no context set and parent not found"
        )
    logger.warning(
        "Tenant context missing in a bot tool; derived tenant_id=%s "
        "from parent_id=%s. Whichever caller invoked the tool forgot "
        "to set the context — check the dispatcher.",
        user.tenant_id, parent_id,
    )
    return user.tenant_id


async def _verify_parent_owns_child(
    db: AsyncSession, parent_id: uuid.UUID, child_id: uuid.UUID
) -> None:
    """Raise ForbiddenException if the parent isn't linked to this child.

    This is the tool layer's authorization boundary — every tool that takes
    a child_id calls it before touching the child's data. Do not skip it,
    even when the caller "already knows" the parent owns the child, because
    Phase 2C will let an LLM invent child_ids.
    """
    result = await db.execute(
        select(ParentStudent.id).where(
            ParentStudent.parent_id == parent_id,
            ParentStudent.student_id == child_id,
        ).limit(1)
    )
    if result.scalar_one_or_none() is None:
        raise ForbiddenException(
            "This child is not linked to your account."
        )


def _primary_teacher_name(school_class: SchoolClass | None) -> str | None:
    """Return the primary teacher's display name, or None if no class /
    no teacher assigned yet."""
    if school_class is None:
        return None
    tc = school_class.primary_teacher  # property, handles is_primary fallback
    if tc is None or tc.teacher is None:
        return None
    return f"{tc.teacher.first_name} {tc.teacher.last_name}".strip()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

async def get_my_children(
    db: AsyncSession, parent_id: uuid.UUID
) -> list[ChildSummary]:
    """Return the parent's children with class + teacher context.

    Uses the same query as StudentService.get_my_children but eager-loads
    teacher_classes.teacher too, since the menu bot displays the teacher
    name alongside the child's name so parents can tell whose class they're
    looking at.
    """
    tenant_id = await _resolve_tenant_id(db, parent_id)

    result = await db.execute(
        select(Student)
        .join(ParentStudent, ParentStudent.student_id == Student.id)
        .where(
            ParentStudent.parent_id == parent_id,
            Student.tenant_id == tenant_id,
            Student.deleted_at.is_(None),
        )
        .options(
            selectinload(Student.school_class)
            .selectinload(SchoolClass.teacher_classes)
            .selectinload(__import__(
                "app.models", fromlist=["TeacherClass"]
            ).TeacherClass.teacher)
        )
        .order_by(Student.first_name, Student.last_name)
    )
    students = list(result.scalars().all())

    return [
        ChildSummary(
            id=s.id,
            first_name=s.first_name,
            last_name=s.last_name,
            class_name=s.school_class.name if s.school_class else None,
            teacher_name=_primary_teacher_name(s.school_class),
        )
        for s in students
    ]


async def get_child_balance(
    db: AsyncSession, parent_id: uuid.UUID, child_id: uuid.UUID
) -> BalanceInfo:
    """Return outstanding balance + unpaid invoices for one child.

    Filters out DRAFT (parents should never see those) and CANCELLED.
    Only rows with a positive ``balance`` are treated as unpaid.
    """
    await _verify_parent_owns_child(db, parent_id, child_id)

    from app.models import BillingInvoice, Tenant

    tenant_id = await _resolve_tenant_id(db, parent_id)

    result = await db.execute(
        select(BillingInvoice)
        .where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.student_id == child_id,
            BillingInvoice.deleted_at.is_(None),
            BillingInvoice.status.not_in(("DRAFT", "CANCELLED")),
            BillingInvoice.balance > 0,
        )
        .order_by(BillingInvoice.due_date.asc().nulls_last())
    )
    invoices = list(result.scalars().all())

    unpaid = [
        {
            "number": inv.invoice_number,
            "due_date": inv.due_date,
            "balance": inv.balance,
            "status": inv.status,
        }
        for inv in invoices
    ]
    total_outstanding = sum(
        (inv["balance"] for inv in unpaid), Decimal("0")
    )

    # Currency lives on tenant.settings — the invoice row itself has no
    # currency column (single-currency per tenant is the current design).
    tenant = await db.get(Tenant, tenant_id)
    currency = "ZAR"
    if tenant is not None:
        currency = (tenant.settings or {}).get("billing_currency") or "ZAR"

    child = await db.get(Student, child_id)
    child_name = (
        f"{child.first_name} {child.last_name}".strip()
        if child is not None else "(child)"
    )

    return BalanceInfo(
        child_name=child_name,
        total_outstanding=total_outstanding,
        currency=currency,
        unpaid_invoices=unpaid,
    )


async def get_child_attendance(
    db: AsyncSession,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    days: int = 7,
) -> AttendanceSummary:
    """Return the child's attendance records for the last N days.

    Days without a recorded attendance row are skipped — no synthetic
    entries. If the school hasn't marked attendance recently the list
    will be short; the caller should note that in the reply.
    """
    await _verify_parent_owns_child(db, parent_id, child_id)

    from app.services.attendance_service import get_attendance_service

    date_to = date.today()
    date_from = date_to - timedelta(days=max(days, 1))

    records, _total, _summary = await get_attendance_service().get_student_attendance_history(
        db=db,
        student_id=child_id,
        date_from=date_from,
        date_to=date_to,
        page=1,
        page_size=days + 1,  # small buffer; days are rare enough to fit
    )

    child = await db.get(Student, child_id)
    child_name = (
        f"{child.first_name} {child.last_name}".strip()
        if child is not None else "(child)"
    )

    return AttendanceSummary(
        child_name=child_name,
        days_shown=days,
        records=[
            AttendanceDay(
                date=r.date,
                status=r.status,
                check_in_time=r.check_in_time,
                notes=r.notes,
            )
            for r in records
        ],
    )


async def get_child_latest_report(
    db: AsyncSession, parent_id: uuid.UUID, child_id: uuid.UUID
) -> ReportSummary | None:
    """Most recent FINALIZED daily_report for the child, or None if the
    school hasn't finalized any yet.
    """
    await _verify_parent_owns_child(db, parent_id, child_id)

    from app.config import get_settings

    tenant_id = await _resolve_tenant_id(db, parent_id)
    result = await db.execute(
        select(DailyReport)
        .where(
            DailyReport.tenant_id == tenant_id,
            DailyReport.student_id == child_id,
            DailyReport.deleted_at.is_(None),
            DailyReport.status == "FINALIZED",
        )
        .order_by(
            DailyReport.finalized_at.desc().nulls_last(),
            DailyReport.report_date.desc(),
        )
        .limit(1)
    )
    report = result.scalar_one_or_none()
    if report is None:
        return None

    child = await db.get(Student, child_id)
    child_name = (
        f"{child.first_name} {child.last_name}".strip()
        if child is not None else "(child)"
    )
    base_url = get_settings().app_base_url.rstrip("/")

    return ReportSummary(
        child_name=child_name,
        report_date=report.report_date,
        finalized_at=report.finalized_at,
        url=f"{base_url}/reports/{report.id}",
    )


async def get_child_teacher(
    db: AsyncSession, parent_id: uuid.UUID, child_id: uuid.UUID
) -> TeacherInfo:
    """Return the primary teacher for the child's class.

    ``teacher_name`` and ``class_name`` can each be None (child not in a
    class yet; class has no teacher assigned) — the caller decides how to
    phrase the "not set up yet" case.
    """
    await _verify_parent_owns_child(db, parent_id, child_id)

    result = await db.execute(
        select(Student)
        .where(Student.id == child_id)
        .options(
            selectinload(Student.school_class)
            .selectinload(SchoolClass.teacher_classes)
            .selectinload(__import__(
                "app.models", fromlist=["TeacherClass"]
            ).TeacherClass.teacher)
        )
    )
    student = result.scalar_one_or_none()
    if student is None:
        # Shouldn't happen — _verify_parent_owns_child ran already — but
        # be defensive since the join is separate from the ownership check.
        raise ForbiddenException("Child not found.")

    return TeacherInfo(
        child_name=f"{student.first_name} {student.last_name}".strip(),
        teacher_name=_primary_teacher_name(student.school_class),
        class_name=student.school_class.name if student.school_class else None,
    )


async def get_recent_announcements(
    db: AsyncSession, parent_id: uuid.UUID, limit: int = 5
) -> list[AnnouncementItem]:
    """Return the most recent non-expired announcements for the parent's
    children.

    Named "get_recent_announcements" instead of "get_upcoming_events"
    because ClassUp doesn't have a calendar/event model — announcements
    are the closest fit and they're what schools actually use to tell
    parents about upcoming things (meetings, holidays, deadlines).

    Filters:
      - Not deleted, not expired.
      - School-wide (class_id NULL) OR posted to one of the parent's
        children's classes.
    """
    tenant_id = await _resolve_tenant_id(db, parent_id)

    # Get the classes this parent's children are in (unique, non-null).
    child_class_ids_result = await db.execute(
        select(Student.class_id)
        .join(ParentStudent, ParentStudent.student_id == Student.id)
        .where(
            ParentStudent.parent_id == parent_id,
            Student.tenant_id == tenant_id,
            Student.class_id.is_not(None),
            Student.deleted_at.is_(None),
        )
    )
    child_class_ids = [cid for cid in child_class_ids_result.scalars().all() if cid is not None]

    now = datetime.utcnow()
    q = (
        select(Announcement)
        .options(selectinload(Announcement.school_class))
        .where(
            Announcement.tenant_id == tenant_id,
            Announcement.deleted_at.is_(None),
            (Announcement.expires_at.is_(None)) | (Announcement.expires_at > now),
        )
    )
    if child_class_ids:
        # School-wide OR one of the parent's classes.
        q = q.where(
            (Announcement.class_id.is_(None))
            | (Announcement.class_id.in_(child_class_ids))
        )
    else:
        # No classes → school-wide only.
        q = q.where(Announcement.class_id.is_(None))

    q = q.order_by(
        Announcement.is_pinned.desc(),
        Announcement.created_at.desc(),
    ).limit(limit)

    result = await db.execute(q)
    rows = list(result.scalars().all())

    return [
        AnnouncementItem(
            title=a.title,
            body=a.body or "",
            is_pinned=a.is_pinned,
            created_at=a.created_at,
            class_name=a.school_class.name if a.school_class else None,
        )
        for a in rows
    ]
