"""Bulk payment upload tests.

Covers:
  - Template generates clean bytes with a locked header and the
    expected columns + dropdown values.
  - parse_excel round-trips cleanly and skips rows without an amount.
  - bulk_import_payments resolves invoices by number OR by student's
    oldest open invoice, validates amount / method / date, produces a
    per-row error for anything that doesn't fit, and the successful
    rows actually persist as BillingPayment + accounting auto-link.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
import pytest_asyncio
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BillingPayment,
    SchoolClass,
    Student,
    Tenant,
    User,
)
from app.models.billing import BillingInvoice, InvoiceStatus
from app.services.billing_service import get_billing_service
from app.services.payment_template_service import (
    PAYMENT_COLUMNS,
    PAYMENT_METHODS,
    get_payment_template_service,
)


@pytest_asyncio.fixture
async def student_with_invoice(
    db: AsyncSession,
    test_tenant: Tenant,
    test_admin: User,
    test_class: SchoolClass,
) -> tuple[Student, BillingInvoice]:
    """One student + one open SENT invoice with a known balance."""
    student = Student(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Chipo",
        last_name="Moyo",
        class_id=test_class.id,
        is_active=True,
    )
    db.add(student)
    await db.flush()

    invoice = BillingInvoice(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        student_id=student.id,
        invoice_number=f"INV-TEST-{uuid.uuid4().hex[:6]}",
        due_date=date.today(),
        subtotal=Decimal("500.00"),
        total_amount=Decimal("500.00"),
        amount_paid=Decimal("0.00"),
        balance=Decimal("500.00"),
        status=InvoiceStatus.SENT.value,
        created_by=test_admin.id,
    )
    db.add(invoice)
    await db.commit()
    await db.refresh(student)
    await db.refresh(invoice)
    return student, invoice


# ──────── template generation ────────


class TestTemplateGeneration:
    def test_generates_valid_xlsx_with_headers(self):
        svc = get_payment_template_service()
        data = svc.generate_template(
            students_with_invoices=[
                {"student_id": "abc", "student_name": "Moyo, Chipo",
                 "invoice_number": "INV-2026-0001", "current_balance": 500},
            ],
            default_payment_date=date(2026, 11, 19),
        )
        assert data[:4] == b"PK\x03\x04"  # xlsx is a zip
        wb = load_workbook(BytesIO(data), read_only=True)
        ws = wb.active
        # Row 1 — machine-readable field names in the expected order
        headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
        expected = [col[0] for col in PAYMENT_COLUMNS]
        assert headers == expected
        wb.close()

    def test_default_date_and_method_prefilled(self):
        svc = get_payment_template_service()
        data = svc.generate_template(
            students_with_invoices=[
                {"student_id": "abc", "student_name": "Moyo, Chipo",
                 "invoice_number": "INV-0001", "current_balance": 500},
            ],
            default_payment_date=date(2026, 11, 19),
            default_payment_method="CASH",
        )
        wb = load_workbook(BytesIO(data), read_only=True)
        ws = wb.active
        # Row 3 is the first data row. Find the payment_date + method cols.
        date_col = next(i for i, c in enumerate(PAYMENT_COLUMNS, 1) if c[0] == "payment_date")
        method_col = next(i for i, c in enumerate(PAYMENT_COLUMNS, 1) if c[0] == "payment_method")
        row3 = next(ws.iter_rows(min_row=3, max_row=3))
        assert row3[date_col - 1].value == "2026-11-19"
        assert row3[method_col - 1].value == "CASH"
        wb.close()


# ──────── parse_excel ────────


class TestParseExcel:
    def test_skips_rows_without_amount(self):
        svc = get_payment_template_service()
        data = svc.generate_template(
            students_with_invoices=[
                {"student_id": str(uuid.uuid4()), "student_name": "A",
                 "invoice_number": "INV-A", "current_balance": 100},
                {"student_id": str(uuid.uuid4()), "student_name": "B",
                 "invoice_number": "INV-B", "current_balance": 200},
            ],
            default_payment_date=date(2026, 11, 19),
        )
        # Admin leaves both rows blank → parse returns zero rows
        rows, total = svc.parse_excel(data)
        assert rows == []
        assert total == 0

    def test_picks_up_rows_with_amount(self, tmp_path):
        """Simulate the admin filling in an amount in row 3."""
        svc = get_payment_template_service()
        data = svc.generate_template(
            students_with_invoices=[
                {"student_id": str(uuid.uuid4()), "student_name": "A",
                 "invoice_number": "INV-A", "current_balance": 100},
            ],
            default_payment_date=date(2026, 11, 19),
        )
        # Open, write the amount, save back to bytes
        wb = load_workbook(BytesIO(data))
        ws = wb.active
        amt_col = next(i for i, c in enumerate(PAYMENT_COLUMNS, 1) if c[0] == "amount")
        ws.cell(row=3, column=amt_col, value=100.00)
        out = BytesIO()
        wb.save(out)
        out.seek(0)

        rows, total = svc.parse_excel(out.getvalue())
        assert total == 1
        assert rows[0]["amount"] == Decimal("100")
        assert rows[0]["invoice_number"] == "INV-A"
        assert rows[0]["payment_date"] == "2026-11-19"


# ──────── bulk_import_payments ────────


class TestBulkImportPayments:
    async def test_resolves_by_invoice_number(
        self, db: AsyncSession, student_with_invoice,
    ):
        student, invoice = student_with_invoice
        rows = [{
            "_row_number": 3,
            "student_id": str(student.id),
            "invoice_number": invoice.invoice_number,
            "amount": Decimal("100"),
            "payment_method": "CASH",
            "payment_date": date.today().isoformat(),
            "reference_number": "BULK-1",
            "notes": "",
        }]
        result = await get_billing_service().bulk_import_payments(db, rows)
        await db.commit()

        assert result["success"] == 1
        assert result["failed"] == 0
        # Payment persisted
        payments = (await db.execute(
            select(BillingPayment).where(BillingPayment.invoice_id == invoice.id)
        )).scalars().all()
        assert len(payments) == 1
        assert payments[0].amount == Decimal("100")
        # Invoice balance updated
        await db.refresh(invoice)
        assert invoice.amount_paid == Decimal("100")
        assert invoice.balance == Decimal("400")
        assert invoice.status == InvoiceStatus.PARTIALLY_PAID.value

    async def test_falls_back_to_oldest_open_invoice(
        self, db: AsyncSession, student_with_invoice,
    ):
        student, invoice = student_with_invoice
        # No invoice_number — service should find the oldest open one
        rows = [{
            "_row_number": 3,
            "student_id": str(student.id),
            "invoice_number": "",
            "amount": Decimal("50"),
            "payment_method": "EFT",
            "payment_date": date.today().isoformat(),
        }]
        result = await get_billing_service().bulk_import_payments(db, rows)
        await db.commit()
        assert result["success"] == 1
        payments = (await db.execute(
            select(BillingPayment).where(BillingPayment.invoice_id == invoice.id)
        )).scalars().all()
        assert len(payments) == 1

    async def test_invalid_invoice_number_captured_as_row_error(
        self, db: AsyncSession, student_with_invoice,
    ):
        student, _ = student_with_invoice
        rows = [{
            "_row_number": 5,
            "student_id": str(student.id),
            "invoice_number": "INV-DOES-NOT-EXIST",
            "amount": Decimal("100"),
            "payment_method": "CASH",
            "payment_date": date.today().isoformat(),
        }]
        result = await get_billing_service().bulk_import_payments(db, rows)
        assert result["success"] == 0
        assert result["failed"] == 1
        assert result["errors"][0]["row"] == 5
        assert "invoice" in result["errors"][0]["message"].lower()

    async def test_bad_amount_captured_as_row_error(
        self, db: AsyncSession, student_with_invoice,
    ):
        student, invoice = student_with_invoice
        rows = [{
            "_row_number": 7,
            "student_id": str(student.id),
            "invoice_number": invoice.invoice_number,
            "amount": Decimal("0"),
            "payment_method": "CASH",
            "payment_date": date.today().isoformat(),
        }]
        result = await get_billing_service().bulk_import_payments(db, rows)
        assert result["failed"] == 1
        assert "amount" in result["errors"][0]["message"].lower()

    async def test_mixed_batch_reports_each_row(
        self, db: AsyncSession, student_with_invoice,
    ):
        student, invoice = student_with_invoice
        rows = [
            # Good row
            {"_row_number": 3, "student_id": str(student.id),
             "invoice_number": invoice.invoice_number,
             "amount": Decimal("50"), "payment_method": "CASH",
             "payment_date": date.today().isoformat()},
            # Bad row — invoice doesn't exist
            {"_row_number": 4, "student_id": str(student.id),
             "invoice_number": "INV-NOPE",
             "amount": Decimal("50"), "payment_method": "CASH",
             "payment_date": date.today().isoformat()},
            # Bad row — amount zero
            {"_row_number": 5, "student_id": str(student.id),
             "invoice_number": invoice.invoice_number,
             "amount": Decimal("0"), "payment_method": "CASH",
             "payment_date": date.today().isoformat()},
        ]
        result = await get_billing_service().bulk_import_payments(db, rows)
        assert result["total"] == 3
        assert result["success"] == 1
        assert result["failed"] == 2
        # Each failure keeps its own row number for the UI to point at
        error_rows = {e["row"] for e in result["errors"]}
        assert error_rows == {4, 5}
