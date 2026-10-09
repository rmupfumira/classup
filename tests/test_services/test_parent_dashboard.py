"""Parent dashboard aggregator — 2026-10-09 redesign.

Covers:
- `build_parent_dashboard` returns the children, a selected child, and
  the three sections (`needs_attention`, `this_week`).
- Unpaid invoices surface with overdue sorted first.
- Pending RSVPs on upcoming events surface in needs_attention; events
  already answered do not.
- Attendance rollup counts the right statuses for the current week.
- An all-caught-up parent gets an empty needs_attention (template
  renders the celebration state).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AttendanceRecord,
    BillingInvoice,
    EventRsvp,
    SchoolClass,
    SchoolEvent,
    Student,
    Tenant,
    User,
)
from app.models.student import ParentStudent
from app.models.user import Role
from app.services.parent_dashboard_service import build_parent_dashboard
from app.utils.security import hash_password


@pytest_asyncio.fixture
async def parent_with_child(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass,
):
    """One parent linked to one child in the test class."""
    parent = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=f"parent-{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password("testpass"),
        first_name="Pat",
        last_name="Parent",
        role=Role.PARENT.value,
        is_active=True,
    )
    student = Student(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Kid",
        last_name="Parent",
        class_id=test_class.id,
        is_active=True,
    )
    db.add_all([parent, student])
    await db.flush()
    link = ParentStudent(
        id=uuid.uuid4(),
        parent_id=parent.id,
        student_id=student.id,
        relationship_type="PARENT",
        is_primary=True,
    )
    db.add(link)
    await db.commit()
    return {"parent": parent, "student": student}


class TestChildrenAndSelection:
    @pytest.mark.asyncio
    async def test_children_list_returned(
        self, db: AsyncSession, test_tenant: Tenant, parent_with_child,
    ):
        parent = parent_with_child["parent"]
        child = parent_with_child["student"]
        pd = await build_parent_dashboard(db, parent.id, test_tenant.id)
        assert len(pd.children) == 1
        assert pd.children[0].id == child.id

    @pytest.mark.asyncio
    async def test_default_selected_child_is_first_alpha(
        self, db: AsyncSession, test_tenant: Tenant, parent_with_child,
    ):
        parent = parent_with_child["parent"]
        pd = await build_parent_dashboard(db, parent.id, test_tenant.id)
        assert pd.selected_child is not None
        assert pd.selected_child.id == parent_with_child["student"].id

    @pytest.mark.asyncio
    async def test_selected_child_override(
        self, db: AsyncSession, test_tenant: Tenant, parent_with_child,
    ):
        parent = parent_with_child["parent"]
        child = parent_with_child["student"]
        pd = await build_parent_dashboard(
            db, parent.id, test_tenant.id, selected_child_id=child.id,
        )
        assert pd.selected_child.id == child.id

    @pytest.mark.asyncio
    async def test_no_children_returns_empty_sections(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """A parent with nothing linked yet still gets a clean object."""
        orphan = User(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            email=f"orphan-{uuid.uuid4().hex[:8]}@test.local",
            password_hash=hash_password("x"),
            first_name="Orphan",
            last_name="Parent",
            role=Role.PARENT.value,
            is_active=True,
        )
        db.add(orphan)
        await db.commit()
        pd = await build_parent_dashboard(db, orphan.id, test_tenant.id)
        assert pd.children == []
        assert pd.selected_child is None
        assert pd.needs_attention == []
        assert pd.this_week == []


class TestNeedsAttentionInvoices:
    @pytest.mark.asyncio
    async def test_overdue_invoice_surfaces_first(
        self, db: AsyncSession, test_tenant: Tenant, parent_with_child,
        test_admin: User,
    ):
        parent = parent_with_child["parent"]
        child = parent_with_child["student"]
        today = date.today()
        overdue = BillingInvoice(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            student_id=child.id,
            invoice_number="INV-TEST-0001",
            billing_period_start=today - timedelta(days=60),
            billing_period_end=today - timedelta(days=30),
            due_date=today - timedelta(days=10),
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
            balance=Decimal("100.00"),
            status="OVERDUE",
            created_by=test_admin.id,
        )
        soon = BillingInvoice(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            student_id=child.id,
            invoice_number="INV-TEST-0002",
            billing_period_start=today,
            billing_period_end=today + timedelta(days=30),
            due_date=today + timedelta(days=3),
            subtotal=Decimal("50.00"),
            total_amount=Decimal("50.00"),
            amount_paid=Decimal("0.00"),
            balance=Decimal("50.00"),
            status="SENT",
            created_by=test_admin.id,
        )
        db.add_all([overdue, soon])
        await db.commit()

        pd = await build_parent_dashboard(db, parent.id, test_tenant.id)
        invoice_items = [i for i in pd.needs_attention if i.kind == "invoice"]
        assert len(invoice_items) == 2
        # Overdue sorts first (ordering_key = -10 < positive days)
        assert "overdue" in invoice_items[0].detail.lower()
        # UI metadata — regressions here hide the whole urgency redesign.
        assert invoice_items[0].urgency == "critical"
        assert invoice_items[0].icon == "invoice"
        assert invoice_items[0].badge_text == "OVERDUE"
        assert invoice_items[0].primary_label == "Pay now"
        # Currency symbol must appear in the title (tenant default USD → $).
        assert "$" in invoice_items[0].title
        assert "100.00" in invoice_items[0].title
        # Due-soon invoice uses the warning tier, not critical.
        assert invoice_items[1].urgency in ("warning", "info")
        assert invoice_items[1].primary_label == "View invoice"

    @pytest.mark.asyncio
    async def test_paid_invoice_not_surfaced(
        self, db: AsyncSession, test_tenant: Tenant, parent_with_child,
        test_admin: User,
    ):
        parent = parent_with_child["parent"]
        child = parent_with_child["student"]
        today = date.today()
        paid = BillingInvoice(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            student_id=child.id,
            invoice_number="INV-TEST-0003",
            billing_period_start=today - timedelta(days=30),
            billing_period_end=today,
            due_date=today + timedelta(days=3),
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            balance=Decimal("0.00"),
            status="PAID",
            created_by=test_admin.id,
        )
        db.add(paid)
        await db.commit()

        pd = await build_parent_dashboard(db, parent.id, test_tenant.id)
        invoice_items = [i for i in pd.needs_attention if i.kind == "invoice"]
        assert invoice_items == []


class TestNeedsAttentionRsvps:
    @pytest.mark.asyncio
    async def test_pending_rsvp_surfaces(
        self, db: AsyncSession, test_tenant: Tenant, parent_with_child,
        test_admin: User,
    ):
        parent = parent_with_child["parent"]
        now = datetime.now(timezone.utc)
        event = SchoolEvent(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            created_by=test_admin.id,
            title="Spring Concert",
            event_type="OTHER",
            scope="SCHOOL",
            starts_at=now + timedelta(days=3),
            timezone="UTC",
            rsvp_required=True,
            rsvp_deadline=now + timedelta(days=2),
        )
        db.add(event)
        await db.commit()

        pd = await build_parent_dashboard(db, parent.id, test_tenant.id)
        rsvp_items = [i for i in pd.needs_attention if i.kind == "event_rsvp"]
        assert len(rsvp_items) == 1
        assert "Spring Concert" in rsvp_items[0].title

    @pytest.mark.asyncio
    async def test_answered_rsvp_not_surfaced(
        self, db: AsyncSession, test_tenant: Tenant, parent_with_child,
        test_admin: User,
    ):
        parent = parent_with_child["parent"]
        now = datetime.now(timezone.utc)
        event = SchoolEvent(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            created_by=test_admin.id,
            title="Already Answered",
            event_type="OTHER",
            scope="SCHOOL",
            starts_at=now + timedelta(days=3),
            timezone="UTC",
            rsvp_required=True,
            rsvp_deadline=now + timedelta(days=2),
        )
        db.add(event)
        await db.flush()
        db.add(EventRsvp(
            id=uuid.uuid4(),
            event_id=event.id,
            user_id=parent.id,
            response="YES",
            responded_at=now,
        ))
        await db.commit()

        pd = await build_parent_dashboard(db, parent.id, test_tenant.id)
        rsvp_items = [i for i in pd.needs_attention if i.kind == "event_rsvp"]
        assert rsvp_items == []


class TestThisWeekAttendance:
    @pytest.mark.asyncio
    async def test_attendance_rollup_counts_week(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass,
        test_admin: User, parent_with_child,
    ):
        parent = parent_with_child["parent"]
        child = parent_with_child["student"]
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        # 2 present days this week
        for offset in (0, 1):
            d = monday + timedelta(days=offset)
            if d > today:
                continue
            db.add(AttendanceRecord(
                id=uuid.uuid4(),
                tenant_id=test_tenant.id,
                student_id=child.id,
                class_id=test_class.id,
                date=d,
                status="PRESENT",
                recorded_by=test_admin.id,
            ))
        await db.commit()

        pd = await build_parent_dashboard(db, parent.id, test_tenant.id)
        att_items = [i for i in pd.this_week if i.kind == "attendance"]
        assert len(att_items) == 1
        assert "present" in att_items[0].title.lower()


class TestAllCaughtUp:
    @pytest.mark.asyncio
    async def test_no_debts_no_rsvps_no_announcements(
        self, db: AsyncSession, test_tenant: Tenant, parent_with_child,
    ):
        parent = parent_with_child["parent"]
        pd = await build_parent_dashboard(db, parent.id, test_tenant.id)
        assert pd.needs_attention == []
