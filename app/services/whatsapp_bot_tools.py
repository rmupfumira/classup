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

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, ValidationError
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error contract — the LLM never sees str(e). Always one of these codes.
# ---------------------------------------------------------------------------

class ToolErrorCode(str, Enum):
    """Codes the AI tool-use loop may return to the model as structured
    errors. The caller promises never to leak raw exception text — that
    was a prompt-injection vector (Principal AI review, 2026-10-08).

    Each code has one, curated, non-templated message string.
    """
    NOT_ALLOWED = "not_allowed"      # ownership / tenant mismatch
    NOT_FOUND = "not_found"          # record genuinely absent
    BAD_INPUT = "bad_input"          # schema / UUID / clamp violation
    UNAVAILABLE = "unavailable"      # upstream PDF/image/service failure
    INTERNAL_ERROR = "internal_error"  # unknown — model gets no detail


_ERROR_MESSAGES = {
    ToolErrorCode.NOT_ALLOWED: "This child is not linked to the parent's account.",
    ToolErrorCode.NOT_FOUND: "No matching record found.",
    ToolErrorCode.BAD_INPUT: "The tool input was invalid or out of range.",
    ToolErrorCode.UNAVAILABLE: "The service is temporarily unavailable.",
    ToolErrorCode.INTERNAL_ERROR: "An internal error occurred.",
}


def tool_error(code: ToolErrorCode) -> dict[str, str]:
    """Build the structured error payload fed back to the model.

    Deliberately curated — no ``str(e)``, no field names, no UUIDs,
    no tenant hints. The model gets a code it can branch on and a
    short human-readable sentence. Everything else is logged
    server-side.
    """
    return {"error": code.value, "message": _ERROR_MESSAGES[code]}


# ---------------------------------------------------------------------------
# Pydantic tool-input models — the authoritative contract.
#
# The JSON Schema we hand to Claude is advisory; these models are the
# enforcement point. Server-side clamping (days ≤ 30, limit ≤ 10) lives
# here, not in the dispatcher's ``int(x or 7)`` extractions.
# ---------------------------------------------------------------------------

class EmptyToolInput(BaseModel):
    """For tools with no parameters (get_my_children)."""


class ChildIdInput(BaseModel):
    """Shared by every single-child tool."""
    child_id: uuid.UUID


class AttendanceInput(BaseModel):
    child_id: uuid.UUID
    days: int = Field(default=7, ge=1, le=30)


class AnnouncementsInput(BaseModel):
    limit: int = Field(default=5, ge=1, le=10)


class InvoicePdfInput(BaseModel):
    child_id: uuid.UUID
    # Invoice numbers are human-typed; keep a conservative cap so a
    # hallucinated 10kb string doesn't fan out into the DB query.
    invoice_number: str | None = Field(default=None, max_length=50)


class PhotosInput(BaseModel):
    limit: int = Field(default=3, ge=1, le=3)


# ---------------------------------------------------------------------------
# Untrusted-content labelling for free text that reaches the LLM.
# ---------------------------------------------------------------------------

# Max characters of staff-authored free text we hand to the model per
# field. The threshold is high enough that genuine content fits (a
# teacher's attendance comment or an announcement body is almost always
# well under 500 chars) but low enough to blunt a long payload-smuggling
# attempt (Principal AI review, 2026-10-08).
UNTRUSTED_TEXT_MAX = 500


def label_untrusted(text: str | None, *, source: str = "staff") -> dict | None:
    """Wrap staff/user-authored free text so the model handles it as data.

    Any string that reaches the LLM from a human-editable column — a
    teacher's attendance note, an announcement body, a photo caption —
    is a potential prompt-injection surface. We can't scrub the content
    (schools legitimately write prose), so we wrap it in a structured
    envelope with a ``trust`` marker. The system prompt instructs the
    model to never follow directives inside ``trust="..."`` payloads.

    Returns ``None`` on empty input (don't bother wrapping a null) so
    callers can keep the key out of the tool result entirely.
    """
    if not text:
        return None
    body = str(text)[:UNTRUSTED_TEXT_MAX]
    truncated = len(str(text)) > UNTRUSTED_TEXT_MAX
    return {"content": body, "trust": source, "truncated": truncated}


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


@dataclass(frozen=True)
class DocumentPayload:
    """A file the parent can receive as a real WhatsApp attachment.

    The caller (whatsapp_ai_bot or whatsapp_menu_bot) wraps this into a
    DocumentReply — no direct Meta API access at the tools layer. Keeps
    the security surface tight: tools produce raw bytes + metadata,
    delivery layer handles all Meta interaction.
    """
    file_bytes: bytes
    mime_type: str
    filename: str
    caption: str
    # Human summary of what this attachment IS, for the bot to compose
    # its intro text (e.g. "Invoice INV-2026-0001 for R500").
    summary: str


@dataclass(frozen=True)
class PhotoPayload:
    """One photo ready to send as an actual WhatsApp image."""
    image_bytes: bytes
    mime_type: str
    caption: str
    taken_by: str | None
    class_name: str | None


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
    db: AsyncSession,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    """Raise ForbiddenException if the parent isn't linked to this child
    at this tenant.

    Defence-in-depth: historically this helper only checked
    (parent_id, child_id) on ``parent_students`` and relied on the
    data-model invariant that a parent is only ever linked to
    children in their own tenant. The Principal AI review flagged
    this as a dangerous dependency on an invariant with no DB
    constraint (2026-10-08). The join now dual-keys on
    ``Student.tenant_id`` and filters ``Student.deleted_at IS NULL``
    so a stale child_id cached in Redis conversation history
    (24h TTL) also refuses to resolve after soft-delete.
    """
    result = await db.execute(
        select(ParentStudent.id)
        .join(Student, Student.id == ParentStudent.student_id)
        .where(
            ParentStudent.parent_id == parent_id,
            ParentStudent.student_id == child_id,
            Student.tenant_id == tenant_id,
            Student.deleted_at.is_(None),
        )
        .limit(1)
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
    from app.models import BillingInvoice, Tenant

    tenant_id = await _resolve_tenant_id(db, parent_id)
    await _verify_parent_owns_child(db, parent_id, child_id, tenant_id)

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
    # jurisdiction_service resolves through platform defaults + country
    # registry so a KE or ZW tenant shows KES / USD, not ZAR.
    from app.services import jurisdiction_service
    tenant = await db.get(Tenant, tenant_id)
    jurisdiction = await jurisdiction_service.get_jurisdiction_for_tenant(db, tenant)
    currency = jurisdiction.currency_code

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
    from app.services.attendance_service import get_attendance_service

    tenant_id = await _resolve_tenant_id(db, parent_id)
    await _verify_parent_owns_child(db, parent_id, child_id, tenant_id)

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
    from app.config import get_settings

    tenant_id = await _resolve_tenant_id(db, parent_id)
    await _verify_parent_owns_child(db, parent_id, child_id, tenant_id)

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
    tenant_id = await _resolve_tenant_id(db, parent_id)
    await _verify_parent_owns_child(db, parent_id, child_id, tenant_id)

    # Dual-key the Student fetch by tenant + soft-delete so this follow-up
    # cannot ever surface another tenant's row even if a future bug in the
    # ownership helper let one through (Principal AI review, 2026-10-08).
    result = await db.execute(
        select(Student)
        .where(
            Student.id == child_id,
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


# ---------------------------------------------------------------------------
# Attachment tools — generate documents and images for delivery via WhatsApp
# ---------------------------------------------------------------------------
#
# Security model for every attachment tool:
#   1. parent_id is bound from user context (upstream), never from tool input
#   2. child_id → verified via _verify_parent_owns_child before any query
#   3. When a tool returns bytes, the delivery layer sends via Meta Media API
#      (media_id path, not public URL) — no data URL is ever exposed
#   4. Filenames sanitized at the send layer (whatsapp_service._sanitize_filename)
#   5. File size caps enforced at upload time (100MB doc / 5MB image)


async def get_child_invoice_pdf(
    db: AsyncSession,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    invoice_number: str | None = None,
) -> DocumentPayload | None:
    """Render the PDF for one of a child's invoices and return the bytes.

    If ``invoice_number`` is given, that specific invoice is fetched
    (must belong to the child — enforced by the query filter). If it's
    omitted, the OLDEST OPEN invoice is used (parent asked "send me
    the invoice" without specifying — that's the one they most need
    to pay). Returns None if the child has no matching invoice.

    Reuses the existing ``generate_invoice_pdf`` (the same generator
    that produces the email attachment) so parents get the same PDF
    they'd get by email — no rendering divergence.
    """
    from app.models import BillingInvoice, Tenant
    from app.services.invoice_pdf import generate_invoice_pdf
    from sqlalchemy.orm import selectinload as _sel

    tenant_id = await _resolve_tenant_id(db, parent_id)
    await _verify_parent_owns_child(db, parent_id, child_id, tenant_id)

    filters = [
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.student_id == child_id,
        BillingInvoice.deleted_at.is_(None),
        # Never surface DRAFT to a parent — same rule as get_child_balance.
        BillingInvoice.status.not_in(("DRAFT", "CANCELLED")),
    ]
    if invoice_number:
        filters.append(BillingInvoice.invoice_number == invoice_number)

    q = (
        select(BillingInvoice)
        .where(*filters)
        .options(_sel(BillingInvoice.items))
        .order_by(BillingInvoice.due_date.asc().nulls_last())
        .limit(1)
    )
    result = await db.execute(q)
    invoice = result.scalar_one_or_none()
    if invoice is None:
        return None

    tenant = await db.get(Tenant, tenant_id)
    child = await db.get(Student, child_id)
    child_name = (
        f"{child.first_name} {child.last_name}".strip()
        if child is not None else "your child"
    )

    # Build the same line_items shape billing_service uses when calling
    # generate_invoice_pdf via email — keeps the two rendering paths
    # identical.
    line_items = [
        {
            "description": (li.description or ""),
            "quantity": str(li.quantity or 0),
            "unit_amount": f"{li.unit_amount:.2f}",
            "total_amount": f"{li.total_amount:.2f}",
        }
        for li in (invoice.items or [])
    ]

    from app.services import jurisdiction_service
    jurisdiction = await jurisdiction_service.get_jurisdiction_for_tenant(db, tenant)
    currency = jurisdiction.currency_symbol
    banking_details = (tenant.settings or {}).get("billing_banking_details") or None
    payment_instructions = (tenant.settings or {}).get("billing_payment_instructions") or None

    try:
        pdf_bytes = generate_invoice_pdf(
            invoice_number=invoice.invoice_number,
            student_name=child_name,
            due_date=invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "no due date",
            total_amount=f"{invoice.total_amount:.2f}",
            currency=currency,
            line_items=line_items,
            tenant_name=tenant.name if tenant else "ClassUp",
            tenant_address=tenant.address if tenant else None,
            tenant_phone=tenant.phone if tenant else None,
            tenant_email=tenant.email if tenant else None,
            banking_details=banking_details,
            payment_instructions=payment_instructions,
        )
    except Exception:
        logger.exception(
            "Failed to render PDF for invoice %s", invoice.invoice_number,
        )
        return None

    return DocumentPayload(
        file_bytes=pdf_bytes,
        mime_type="application/pdf",
        # Filename appears in the WhatsApp doc bubble the parent sees —
        # make it self-explanatory when saved to their phone.
        filename=f"{invoice.invoice_number}_{child_name.replace(' ', '_')}.pdf",
        caption=(
            f"Invoice {invoice.invoice_number} for {child_name} "
            f"— {currency} {invoice.total_amount:,.2f} "
            f"(balance {currency} {invoice.balance:,.2f})"
        ),
        summary=(
            f"Invoice {invoice.invoice_number} for {child_name}: "
            f"{currency} {invoice.total_amount:,.2f}"
            + (f", balance {currency} {invoice.balance:,.2f}" if invoice.balance > 0 else " — paid in full")
        ),
    )


async def get_child_report_pdf(
    db: AsyncSession, parent_id: uuid.UUID, child_id: uuid.UUID,
) -> DocumentPayload | None:
    """Render the child's most recent finalised report as a PDF.

    Uses ``app.services.report_pdf.render_report_pdf`` which renders the
    report from its JSONB structure via reportlab (no browser, no
    system deps). Cached to R2 on first render so subsequent parent
    requests are cheap.
    """
    from app.models import DailyReport
    from app.services.report_pdf import render_report_pdf

    tenant_id = await _resolve_tenant_id(db, parent_id)
    await _verify_parent_owns_child(db, parent_id, child_id, tenant_id)

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
        if child is not None else "your child"
    )

    try:
        pdf_bytes = await render_report_pdf(db, report)
    except Exception:
        logger.exception("Failed to render PDF for report %s", report.id)
        return None

    date_str = report.report_date.strftime("%d %b %Y")
    return DocumentPayload(
        file_bytes=pdf_bytes,
        mime_type="application/pdf",
        filename=f"Report_{child_name.replace(' ', '_')}_{report.report_date.isoformat()}.pdf",
        caption=f"{child_name}'s report — {date_str}",
        summary=f"Latest report for {child_name} ({date_str})",
    )


async def get_recent_photos(
    db: AsyncSession, parent_id: uuid.UUID, limit: int = 3,
) -> list[PhotoPayload]:
    """Return recent photos shared with the parent's children's classes.

    Downloads the actual image bytes from R2 (via file_service's presigned
    URL) — the delivery layer then uploads them to Meta's Media API and
    sends as WhatsApp images. Capped at ``limit`` (default 3 — WhatsApp
    handles 4 nicely in a burst but 3 keeps the chat scrollable).
    """
    from app.models import PhotoShare, PhotoShareFile, Student
    from app.services.file_service import get_file_service
    import httpx as _httpx

    tenant_id = await _resolve_tenant_id(db, parent_id)

    # Get parent's children's classes.
    child_classes_result = await db.execute(
        select(Student.class_id)
        .join(ParentStudent, ParentStudent.student_id == Student.id)
        .where(
            ParentStudent.parent_id == parent_id,
            Student.tenant_id == tenant_id,
            Student.deleted_at.is_(None),
        )
    )
    class_ids = [cid for cid in child_classes_result.scalars().all() if cid]

    if not class_ids:
        return []

    # Recent PhotoShares posted to those classes.
    shares_q = (
        select(PhotoShare)
        .where(
            PhotoShare.tenant_id == tenant_id,
            PhotoShare.deleted_at.is_(None),
            PhotoShare.class_id.in_(class_ids),
        )
        .options(
            selectinload(PhotoShare.files).selectinload(PhotoShareFile.file_entity),
        )
        .order_by(PhotoShare.created_at.desc())
        .limit(3)  # up to 3 recent SHARES; we'll pick photos from each
    )
    shares_result = await db.execute(shares_q)
    shares = list(shares_result.scalars().all())

    if not shares:
        return []

    file_service = get_file_service()
    payloads: list[PhotoPayload] = []

    for share in shares:
        if len(payloads) >= limit:
            break
        class_name = None
        try:
            # Class name lookup for caption. If it fails, no worries —
            # the caption just omits it. Tenant-scoped by the class_id we
            # already validated as belonging to the parent's children.
            from app.models import SchoolClass
            cls = await db.get(SchoolClass, share.class_id) if share.class_id else None
            class_name = cls.name if cls else None
        except Exception:
            pass

        for psf in (share.files or []):
            if len(payloads) >= limit:
                break
            fe = psf.file_entity
            if fe is None or fe.content_type not in ("image/jpeg", "image/png"):
                continue
            try:
                url = file_service.generate_presigned_url(fe, expires_in=300)
                # Fetch the bytes ourselves — Meta accepts either a URL
                # or bytes, but we prefer bytes so no signed URL ever
                # touches Meta's infrastructure. Signed URL only used
                # for our own server-to-storage read.
                async with _httpx.AsyncClient(timeout=15.0) as client:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    payloads.append(PhotoPayload(
                        image_bytes=r.content,
                        mime_type=fe.content_type,
                        caption=(share.caption or "")[:200],
                        taken_by=None,
                        class_name=class_name,
                    ))
            except Exception:
                logger.exception(
                    "Failed to fetch photo %s for parent %s", fe.id, parent_id,
                )
                continue

    return payloads
