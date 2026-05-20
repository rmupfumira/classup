"""Tests for billing_service — focused on the bugs we've already hit
in production once. Keeping these tight regression tests around so we
don't regress the same way twice.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
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
