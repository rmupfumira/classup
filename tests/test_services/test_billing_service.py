"""Tests for billing_service — focused on the bugs we've already hit
in production once. Keeping these tight regression tests around so we
don't regress the same way twice.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolClass, Student, Tenant, User
from app.models.billing import (
    BillingFeeItem,
    BillingInvoice,
    FeeFrequency,
    InvoiceStatus,
)
from app.schemas.billing import GenerateInvoicesRequest
from app.services.billing_service import get_billing_service


@pytest_asyncio.fixture
async def fee_items(db: AsyncSession, test_tenant: Tenant) -> list[BillingFeeItem]:
    """Two simple recurring fee items so we exercise the line-item loop."""
    items = []
    for name, amount in (("Tuition", "3500.00"), ("Stationery", "250.00")):
        fi = BillingFeeItem(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name=name,
            description="",
            amount=Decimal(amount),
            frequency=FeeFrequency.MONTHLY.value,
            applies_to="ALL",
            is_active=True,
            display_order=0,
        )
        db.add(fi)
        items.append(fi)
    await db.commit()
    for fi in items:
        await db.refresh(fi)
    return items


@pytest_asyncio.fixture
async def students(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass
) -> list[Student]:
    """A handful of unparented students — covers the bulk-generate scenario
    the tester reported."""
    out = []
    for i in range(4):
        s = Student(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            first_name=f"Kid{i}",
            last_name="Test",
            class_id=test_class.id,
            is_active=True,
        )
        db.add(s)
        out.append(s)
    await db.commit()
    for s in out:
        await db.refresh(s)
    return out


class TestBulkInvoiceGeneration:
    """The bug: looping calls to ``_generate_invoice_number`` inside
    generate_invoices saw the same COUNT (no flush between iterations),
    so every invoice in the batch got the same INV-YYYY-0001 number →
    UNIQUE constraint violation on flush → 500. Counting once + local
    increment is the right behaviour.
    """

    async def test_invoice_numbers_are_unique_across_batch(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        test_admin: User,
        test_class: SchoolClass,
        students: list[Student],
        fee_items: list[BillingFeeItem],
    ):
        service = get_billing_service()
        request = GenerateInvoicesRequest(
            class_id=test_class.id,
            student_ids=[s.id for s in students],
            fee_item_ids=[fi.id for fi in fee_items],
            due_date=date.today() + timedelta(days=14),
        )

        invoices = await service.generate_invoices(db, request)
        await db.commit()

        # 4 invoices created, all with distinct numbers
        assert len(invoices) == 4
        numbers = [inv.invoice_number for inv in invoices]
        assert len(set(numbers)) == 4, f"Duplicate invoice numbers in batch: {numbers}"

        # All numbers follow the INV-YYYY-NNNN pattern and increment 1..N
        year = date.today().year
        expected = {f"INV-{year}-{i:04d}" for i in range(1, 5)}
        assert set(numbers) == expected

    async def test_second_batch_continues_numbering(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        test_admin: User,
        test_class: SchoolClass,
        students: list[Student],
        fee_items: list[BillingFeeItem],
    ):
        """A follow-up batch should pick up where the previous one left off."""
        service = get_billing_service()
        first_request = GenerateInvoicesRequest(
            class_id=test_class.id,
            student_ids=[s.id for s in students[:2]],
            fee_item_ids=[fi.id for fi in fee_items],
            due_date=date.today() + timedelta(days=14),
        )
        first_batch = await service.generate_invoices(db, first_request)
        await db.commit()

        second_request = GenerateInvoicesRequest(
            class_id=test_class.id,
            student_ids=[s.id for s in students[2:]],
            fee_item_ids=[fi.id for fi in fee_items],
            due_date=date.today() + timedelta(days=14),
        )
        second_batch = await service.generate_invoices(db, second_request)
        await db.commit()

        year = date.today().year
        first_numbers = [inv.invoice_number for inv in first_batch]
        second_numbers = [inv.invoice_number for inv in second_batch]
        assert first_numbers == [f"INV-{year}-0001", f"INV-{year}-0002"]
        assert second_numbers == [f"INV-{year}-0003", f"INV-{year}-0004"]

    async def test_unparented_students_still_get_invoices(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        test_admin: User,
        test_class: SchoolClass,
        students: list[Student],
        fee_items: list[BillingFeeItem],
    ):
        """The "students without parents" case — invoice is still created,
        no email/notification sent. Per tester request, this should be a
        silent success path rather than an error."""
        service = get_billing_service()
        request = GenerateInvoicesRequest(
            class_id=test_class.id,
            student_ids=[s.id for s in students],
            fee_item_ids=[fi.id for fi in fee_items],
            due_date=date.today() + timedelta(days=14),
        )

        invoices = await service.generate_invoices(db, request)
        await db.commit()

        assert len(invoices) == 4
        for inv in invoices:
            assert inv.status == InvoiceStatus.SENT.value
            assert inv.total_amount == Decimal("3750.00")  # 3500 + 250

        # The service stashes a count on invoices[0] so the API can surface
        # it in the response message. All 4 students are unparented in this
        # fixture, so the count should be 4.
        no_parent_count = getattr(invoices[0], "_students_without_parents", 0)
        assert no_parent_count == 4


class TestTermAwareInvoicing:
    """applies_to_term + TERMLY description injection — the 2026-11-17
    calendar wiring. A "Term 2 Tuition" fee pinned to term 2 must skip
    when the billing period falls in a different term, and TERMLY lines
    must auto-label the term name + year in the description so parents
    see "Monthly Tuition — Term 2 2026" on the invoice."""

    @pytest_asyncio.fixture
    async def zw_tenant(self, db: AsyncSession) -> AsyncGenerator[Tenant, None]:
        """Zimbabwe-based tenant + its admin, so the billing service's
        get_tenant_id + get_current_user_id both resolve to this tenant
        rather than a shared default from another fixture."""
        from app.utils.tenant_context import (
            _tenant_id as tctx,
            _current_user_id as uctx,
            _current_user_role as rctx,
        )
        from app.utils.security import hash_password
        tid = uuid.uuid4()
        t = Tenant(
            id=tid,
            name=f"ZW School {tid.hex[:6]}",
            slug=f"zw-{tid.hex[:8]}",
            email=f"admin@zw-{tid.hex[:8]}.test",
            education_type="PRIMARY_SCHOOL",
            settings={"country": "ZW", "features": {}},
            is_active=True,
            onboarding_completed=True,
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        admin = User(
            id=uuid.uuid4(),
            tenant_id=t.id,
            email=f"zw-admin-{t.id.hex[:6]}@test.local",
            password_hash=hash_password("x"),
            first_name="ZW", last_name="Admin",
            role="SCHOOL_ADMIN", is_active=True, language="en",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        t_tok = tctx.set(t.id)
        u_tok = uctx.set(admin.id)
        r_tok = rctx.set("SCHOOL_ADMIN")
        try:
            yield t
        finally:
            tctx.reset(t_tok); uctx.reset(u_tok); rctx.reset(r_tok)
            await db.execute(delete(Tenant).where(Tenant.id == t.id))
            await db.commit()

    @pytest_asyncio.fixture
    async def zw_seed(self, db: AsyncSession):
        """Make sure the ZW 2026 calendar is present for these tests."""
        from app.services import calendar_service
        await calendar_service.seed_zimbabwe_2026_if_missing(db)
        await db.commit()
        yield

    async def test_termly_description_gets_labelled(
        self,
        db: AsyncSession,
        zw_tenant: Tenant,
        zw_seed,
    ):
        """A TERMLY fee item gets "— Term X 2026" appended on its line."""
        # Single fee item, TERMLY, applies to all.
        fi = BillingFeeItem(
            id=uuid.uuid4(),
            tenant_id=zw_tenant.id,
            name="Tuition",
            description="",
            amount=Decimal("1200.00"),
            frequency=FeeFrequency.TERMLY.value,
            applies_to="ALL",
            is_active=True,
            display_order=0,
        )
        cls = SchoolClass(
            id=uuid.uuid4(),
            tenant_id=zw_tenant.id,
            name="Grade 1",
            is_active=True,
        )
        s = Student(
            id=uuid.uuid4(),
            tenant_id=zw_tenant.id,
            first_name="Alice",
            last_name="M",
            class_id=cls.id,
            is_active=True,
        )
        db.add_all([fi, cls, s])
        await db.commit()
        await db.refresh(fi); await db.refresh(cls); await db.refresh(s)

        service = get_billing_service()
        # Feb 14, 2026 lands inside ZW Term 1 (Jan 13 → Apr 10).
        req = GenerateInvoicesRequest(
            class_id=cls.id,
            student_ids=[s.id],
            fee_item_ids=[fi.id],
            billing_period_start=date(2026, 2, 1),
            billing_period_end=date(2026, 2, 28),
            due_date=date(2026, 2, 28),
        )
        invoices = await service.generate_invoices(db, req)
        await db.commit()

        assert len(invoices) == 1
        assert len(invoices[0].items) == 1
        desc = invoices[0].items[0].description
        assert "Term 1" in desc and "2026" in desc, f"got: {desc!r}"

    async def test_applies_to_term_filters_out_wrong_term(
        self,
        db: AsyncSession,
        zw_tenant: Tenant,
        zw_seed,
    ):
        """A fee pinned to Term 2 must be skipped when the invoice period
        is in Term 1 — otherwise the parent sees the wrong charge."""
        term2_only = BillingFeeItem(
            id=uuid.uuid4(),
            tenant_id=zw_tenant.id,
            name="Term 2 Camp",
            description="",
            amount=Decimal("500.00"),
            frequency=FeeFrequency.TERMLY.value,
            applies_to="ALL",
            applies_to_term=2,
            is_active=True,
            display_order=0,
        )
        any_term = BillingFeeItem(
            id=uuid.uuid4(),
            tenant_id=zw_tenant.id,
            name="Tuition",
            description="",
            amount=Decimal("1200.00"),
            frequency=FeeFrequency.TERMLY.value,
            applies_to="ALL",
            applies_to_term=None,
            is_active=True,
            display_order=1,
        )
        cls = SchoolClass(
            id=uuid.uuid4(), tenant_id=zw_tenant.id, name="Grade 2", is_active=True,
        )
        s = Student(
            id=uuid.uuid4(), tenant_id=zw_tenant.id, first_name="Bob", last_name="K",
            class_id=cls.id, is_active=True,
        )
        db.add_all([term2_only, any_term, cls, s])
        await db.commit()
        for row in (term2_only, any_term, cls, s):
            await db.refresh(row)

        service = get_billing_service()
        # Feb 14 is Term 1 — Term 2 Camp should be skipped, Tuition kept.
        req = GenerateInvoicesRequest(
            class_id=cls.id,
            student_ids=[s.id],
            fee_item_ids=[term2_only.id, any_term.id],
            billing_period_start=date(2026, 2, 14),
            billing_period_end=date(2026, 2, 28),
            due_date=date(2026, 2, 28),
        )
        invoices = await service.generate_invoices(db, req)
        await db.commit()

        assert len(invoices) == 1
        names = [li.description for li in invoices[0].items]
        assert any("Tuition" in n for n in names)
        assert not any("Term 2 Camp" in n for n in names), (
            f"Term 2 fee should not have been billed in a Term 1 period: {names}"
        )
        # Only the Tuition line → subtotal 1200, not 1700.
        assert invoices[0].total_amount == Decimal("1200.00")
