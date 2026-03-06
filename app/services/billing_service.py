"""Billing service for fee management, invoicing, and payments."""

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.models.billing import (
    BillingFeeItem,
    BillingInvoice,
    BillingInvoiceItem,
    BillingPayment,
    InvoiceStatus,
)
from app.models.student import ParentStudent, Student
from app.models.user import User
from app.schemas.billing import (
    BillingSummary,
    ChildBalance,
    FeeItemCreate,
    FeeItemUpdate,
    GenerateInvoicesRequest,
    InvoiceCreate,
    InvoiceUpdate,
    PaymentCreate,
    StatementEntry,
    StudentStatement,
)
from app.utils.tenant_context import get_current_user_id, get_tenant_id

logger = logging.getLogger(__name__)


class BillingService:
    """Service for all billing operations."""

    # =========================================================================
    # Fee Items
    # =========================================================================

    async def get_fee_items(
        self,
        db: AsyncSession,
        is_active: bool | None = None,
        class_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[BillingFeeItem], int]:
        """List fee items for the current tenant."""
        tenant_id = get_tenant_id()

        query = select(BillingFeeItem).where(
            BillingFeeItem.tenant_id == tenant_id,
            BillingFeeItem.deleted_at.is_(None),
        )

        if is_active is not None:
            query = query.where(BillingFeeItem.is_active == is_active)
        if class_id is not None:
            query = query.where(BillingFeeItem.class_id == class_id)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = query.order_by(
            BillingFeeItem.display_order, BillingFeeItem.name
        )
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        return list(result.scalars().all()), total

    async def get_fee_item(
        self, db: AsyncSession, fee_item_id: uuid.UUID
    ) -> BillingFeeItem:
        """Get a single fee item."""
        tenant_id = get_tenant_id()
        query = select(BillingFeeItem).where(
            BillingFeeItem.id == fee_item_id,
            BillingFeeItem.tenant_id == tenant_id,
            BillingFeeItem.deleted_at.is_(None),
        )
        result = await db.execute(query)
        item = result.scalar_one_or_none()
        if not item:
            raise NotFoundException("Fee item not found")
        return item

    async def create_fee_item(
        self, db: AsyncSession, data: FeeItemCreate
    ) -> BillingFeeItem:
        """Create a new fee item."""
        tenant_id = get_tenant_id()

        item = BillingFeeItem(
            tenant_id=tenant_id,
            name=data.name,
            description=data.description,
            amount=data.amount,
            frequency=data.frequency.value,
            applies_to=data.applies_to.value,
            class_id=data.class_id,
            is_active=data.is_active,
            display_order=data.display_order,
        )
        db.add(item)
        await db.flush()
        await db.refresh(item)
        return item

    async def update_fee_item(
        self, db: AsyncSession, fee_item_id: uuid.UUID, data: FeeItemUpdate
    ) -> BillingFeeItem:
        """Update a fee item."""
        item = await self.get_fee_item(db, fee_item_id)

        update_data = data.model_dump(exclude_unset=True)
        if "frequency" in update_data and update_data["frequency"] is not None:
            update_data["frequency"] = update_data["frequency"].value
        if "applies_to" in update_data and update_data["applies_to"] is not None:
            update_data["applies_to"] = update_data["applies_to"].value

        for key, value in update_data.items():
            setattr(item, key, value)

        await db.flush()
        await db.refresh(item)
        return item

    async def delete_fee_item(
        self, db: AsyncSession, fee_item_id: uuid.UUID
    ) -> None:
        """Soft-delete a fee item."""
        item = await self.get_fee_item(db, fee_item_id)
        item.deleted_at = datetime.now(timezone.utc)
        await db.flush()

    # =========================================================================
    # Invoice Number Generation
    # =========================================================================

    async def _generate_invoice_number(self, db: AsyncSession) -> str:
        """Generate the next invoice number for the tenant: INV-YYYY-NNNN."""
        tenant_id = get_tenant_id()
        year = date.today().year
        prefix = f"INV-{year}-"

        query = (
            select(func.count())
            .select_from(BillingInvoice)
            .where(
                BillingInvoice.tenant_id == tenant_id,
                BillingInvoice.invoice_number.like(f"{prefix}%"),
            )
        )
        count = (await db.execute(query)).scalar() or 0
        return f"{prefix}{count + 1:04d}"

    # =========================================================================
    # Invoices
    # =========================================================================

    async def get_invoices(
        self,
        db: AsyncSession,
        student_id: uuid.UUID | None = None,
        class_id: uuid.UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[BillingInvoice], int]:
        """List invoices with optional filters."""
        tenant_id = get_tenant_id()

        query = select(BillingInvoice).where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.deleted_at.is_(None),
        )

        if student_id:
            query = query.where(BillingInvoice.student_id == student_id)
        if class_id:
            query = query.join(Student, BillingInvoice.student_id == Student.id).where(
                Student.class_id == class_id
            )
        if status:
            query = query.where(BillingInvoice.status == status)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = query.order_by(BillingInvoice.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        return list(result.scalars().unique().all()), total

    async def get_invoice(
        self, db: AsyncSession, invoice_id: uuid.UUID
    ) -> BillingInvoice:
        """Get a single invoice with items and payments."""
        tenant_id = get_tenant_id()
        query = select(BillingInvoice).where(
            BillingInvoice.id == invoice_id,
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.deleted_at.is_(None),
        )
        result = await db.execute(query)
        invoice = result.scalar_one_or_none()
        if not invoice:
            raise NotFoundException("Invoice not found")
        return invoice

    async def create_invoice(
        self, db: AsyncSession, data: InvoiceCreate
    ) -> BillingInvoice:
        """Create a single invoice with line items."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()
        invoice_number = await self._generate_invoice_number(db)

        subtotal = Decimal("0.00")
        invoice_items = []
        for item_data in data.items:
            line_total = item_data.unit_amount * item_data.quantity
            subtotal += line_total
            invoice_items.append(
                BillingInvoiceItem(
                    fee_item_id=item_data.fee_item_id,
                    description=item_data.description,
                    quantity=item_data.quantity,
                    unit_amount=item_data.unit_amount,
                    total_amount=line_total,
                )
            )

        invoice = BillingInvoice(
            tenant_id=tenant_id,
            student_id=data.student_id,
            invoice_number=invoice_number,
            billing_period_start=data.billing_period_start,
            billing_period_end=data.billing_period_end,
            due_date=data.due_date,
            subtotal=subtotal,
            total_amount=subtotal,
            amount_paid=Decimal("0.00"),
            balance=subtotal,
            status=InvoiceStatus.DRAFT.value,
            notes=data.notes,
            created_by=user_id,
            items=invoice_items,
        )
        db.add(invoice)
        await db.flush()
        await db.refresh(invoice)
        return invoice

    async def update_invoice(
        self, db: AsyncSession, invoice_id: uuid.UUID, data: InvoiceUpdate
    ) -> BillingInvoice:
        """Update a draft invoice."""
        invoice = await self.get_invoice(db, invoice_id)
        if invoice.status != InvoiceStatus.DRAFT.value:
            raise ValidationException("Only draft invoices can be edited")

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(invoice, key, value)

        await db.flush()
        await db.refresh(invoice)
        return invoice

    async def delete_invoice(
        self, db: AsyncSession, invoice_id: uuid.UUID
    ) -> None:
        """Soft-delete a draft invoice."""
        invoice = await self.get_invoice(db, invoice_id)
        if invoice.status != InvoiceStatus.DRAFT.value:
            raise ValidationException("Only draft invoices can be deleted")
        invoice.deleted_at = datetime.now(timezone.utc)
        await db.flush()

    async def send_invoice(
        self, db: AsyncSession, invoice_id: uuid.UUID
    ) -> BillingInvoice:
        """Mark invoice as sent and notify parents."""
        invoice = await self.get_invoice(db, invoice_id)
        if invoice.status not in (InvoiceStatus.DRAFT.value, InvoiceStatus.SENT.value):
            raise ValidationException("Invoice cannot be sent in its current status")

        invoice.status = InvoiceStatus.SENT.value
        invoice.sent_at = datetime.now(timezone.utc)
        await db.flush()

        # Email and notify parents
        try:
            await self._email_parents_invoice(db, invoice)
        except Exception:
            logger.exception("Failed to email parents for invoice %s", invoice_id)
        try:
            await self._notify_parents_invoice(db, invoice)
        except Exception:
            logger.exception("Failed to notify parents for invoice %s", invoice_id)

        await db.refresh(invoice)
        return invoice

    async def cancel_invoice(
        self, db: AsyncSession, invoice_id: uuid.UUID
    ) -> BillingInvoice:
        """Cancel an invoice."""
        invoice = await self.get_invoice(db, invoice_id)
        if invoice.status == InvoiceStatus.PAID.value:
            raise ValidationException("Cannot cancel a fully paid invoice")
        if invoice.status == InvoiceStatus.CANCELLED.value:
            raise ValidationException("Invoice is already cancelled")

        invoice.status = InvoiceStatus.CANCELLED.value
        await db.flush()
        await db.refresh(invoice)
        return invoice

    # =========================================================================
    # Batch Generation
    # =========================================================================

    async def generate_invoices(
        self, db: AsyncSession, data: GenerateInvoicesRequest
    ) -> list[BillingInvoice]:
        """Generate invoices for multiple students from selected fee items."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Load fee items
        fee_items_query = select(BillingFeeItem).where(
            BillingFeeItem.id.in_(data.fee_item_ids),
            BillingFeeItem.tenant_id == tenant_id,
            BillingFeeItem.deleted_at.is_(None),
        )
        fee_items = list((await db.execute(fee_items_query)).scalars().all())
        if not fee_items:
            raise ValidationException("No valid fee items selected")

        invoices = []
        for student_id in data.student_ids:
            invoice_number = await self._generate_invoice_number(db)

            subtotal = Decimal("0.00")
            line_items = []
            for fi in fee_items:
                line_total = fi.amount * 1  # qty=1
                subtotal += line_total
                line_items.append(
                    BillingInvoiceItem(
                        fee_item_id=fi.id,
                        description=fi.name,
                        quantity=1,
                        unit_amount=fi.amount,
                        total_amount=line_total,
                    )
                )

            invoice = BillingInvoice(
                tenant_id=tenant_id,
                student_id=student_id,
                invoice_number=invoice_number,
                billing_period_start=data.billing_period_start,
                billing_period_end=data.billing_period_end,
                due_date=data.due_date,
                subtotal=subtotal,
                total_amount=subtotal,
                amount_paid=Decimal("0.00"),
                balance=subtotal,
                status=InvoiceStatus.SENT.value,
                sent_at=datetime.now(timezone.utc),
                created_by=user_id,
                items=line_items,
            )
            db.add(invoice)
            invoices.append(invoice)

        await db.flush()
        for inv in invoices:
            await db.refresh(inv)

        # Auto-send email and in-app notification to parents
        for inv in invoices:
            try:
                await self._email_parents_invoice(db, inv)
            except Exception:
                logger.exception("Failed to email parents for invoice %s", inv.id)
            try:
                await self._notify_parents_invoice(db, inv)
            except Exception:
                logger.exception("Failed to notify parents for invoice %s", inv.id)

        return invoices

    # =========================================================================
    # Payments
    # =========================================================================

    async def get_payments(
        self,
        db: AsyncSession,
        student_id: uuid.UUID | None = None,
        invoice_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[BillingPayment], int]:
        """List payments."""
        tenant_id = get_tenant_id()

        query = select(BillingPayment).where(
            BillingPayment.tenant_id == tenant_id,
            BillingPayment.deleted_at.is_(None),
        )

        if student_id:
            query = query.where(BillingPayment.student_id == student_id)
        if invoice_id:
            query = query.where(BillingPayment.invoice_id == invoice_id)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = query.order_by(BillingPayment.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        return list(result.scalars().all()), total

    async def record_payment(
        self, db: AsyncSession, data: PaymentCreate
    ) -> BillingPayment:
        """Record a payment and update invoice balances."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        invoice = await self.get_invoice(db, data.invoice_id)
        if invoice.status in (
            InvoiceStatus.PAID.value,
            InvoiceStatus.CANCELLED.value,
        ):
            raise ValidationException(
                f"Cannot record payment on a {invoice.status} invoice"
            )

        if data.amount > invoice.balance:
            raise ValidationException(
                f"Payment amount ({data.amount}) exceeds invoice balance ({invoice.balance})"
            )

        payment = BillingPayment(
            tenant_id=tenant_id,
            invoice_id=invoice.id,
            student_id=invoice.student_id,
            amount=data.amount,
            payment_method=data.payment_method.value,
            reference_number=data.reference_number,
            payment_date=data.payment_date,
            notes=data.notes,
            recorded_by=user_id,
        )
        db.add(payment)

        # Update invoice denormalized fields
        invoice.amount_paid += data.amount
        invoice.balance = invoice.total_amount - invoice.amount_paid

        if invoice.balance <= Decimal("0.00"):
            invoice.status = InvoiceStatus.PAID.value
            invoice.balance = Decimal("0.00")
        elif invoice.amount_paid > Decimal("0.00"):
            invoice.status = InvoiceStatus.PARTIALLY_PAID.value

        await db.flush()

        # Notify parents
        try:
            await self._notify_parents_payment(db, payment, invoice)
        except Exception:
            logger.exception("Failed to notify parents for payment on invoice %s", invoice.id)

        await db.refresh(payment)
        return payment

    async def delete_payment(
        self, db: AsyncSession, payment_id: uuid.UUID
    ) -> None:
        """Delete a payment and recalculate invoice balance."""
        tenant_id = get_tenant_id()

        query = select(BillingPayment).where(
            BillingPayment.id == payment_id,
            BillingPayment.tenant_id == tenant_id,
            BillingPayment.deleted_at.is_(None),
        )
        result = await db.execute(query)
        payment = result.scalar_one_or_none()
        if not payment:
            raise NotFoundException("Payment not found")

        invoice = await self.get_invoice(db, payment.invoice_id)

        # Soft-delete the payment
        payment.deleted_at = datetime.now(timezone.utc)

        # Recalculate invoice totals
        invoice.amount_paid -= payment.amount
        if invoice.amount_paid < Decimal("0.00"):
            invoice.amount_paid = Decimal("0.00")
        invoice.balance = invoice.total_amount - invoice.amount_paid

        # Update status
        if invoice.balance <= Decimal("0.00"):
            invoice.status = InvoiceStatus.PAID.value
        elif invoice.amount_paid > Decimal("0.00"):
            invoice.status = InvoiceStatus.PARTIALLY_PAID.value
        else:
            invoice.status = InvoiceStatus.SENT.value

        await db.flush()

    # =========================================================================
    # Statement
    # =========================================================================

    async def get_student_statement(
        self, db: AsyncSession, student_id: uuid.UUID
    ) -> StudentStatement:
        """Build a chronological statement for a student."""
        tenant_id = get_tenant_id()

        # Get student
        student = (
            await db.execute(
                select(Student).where(
                    Student.id == student_id,
                    Student.tenant_id == tenant_id,
                    Student.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not student:
            raise NotFoundException("Student not found")

        # Get all non-cancelled invoices
        invoices_q = select(BillingInvoice).where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.student_id == student_id,
            BillingInvoice.deleted_at.is_(None),
            BillingInvoice.status != InvoiceStatus.CANCELLED.value,
        )
        invoices = list((await db.execute(invoices_q)).scalars().unique().all())

        # Get all payments
        payments_q = select(BillingPayment).where(
            BillingPayment.tenant_id == tenant_id,
            BillingPayment.student_id == student_id,
            BillingPayment.deleted_at.is_(None),
        )
        payments = list((await db.execute(payments_q)).scalars().all())

        # Build entries
        entries: list[StatementEntry] = []

        for inv in invoices:
            entries.append(
                StatementEntry(
                    date=inv.created_at.date() if hasattr(inv.created_at, 'date') else inv.due_date,
                    type="INVOICE",
                    description=f"Invoice {inv.invoice_number}",
                    reference=inv.invoice_number,
                    debit=inv.total_amount,
                    credit=Decimal("0.00"),
                    entity_id=inv.id,
                )
            )

        for pmt in payments:
            inv_number = ""
            for inv in invoices:
                if inv.id == pmt.invoice_id:
                    inv_number = inv.invoice_number
                    break
            entries.append(
                StatementEntry(
                    date=pmt.payment_date,
                    type="PAYMENT",
                    description=f"Payment - {pmt.payment_method}",
                    reference=pmt.reference_number or inv_number,
                    debit=Decimal("0.00"),
                    credit=pmt.amount,
                    entity_id=pmt.id,
                )
            )

        # Sort chronologically
        entries.sort(key=lambda e: (e.date, 0 if e.type == "INVOICE" else 1))

        # Calculate running balance
        running = Decimal("0.00")
        for entry in entries:
            running += entry.debit - entry.credit
            entry.running_balance = running

        total_invoiced = sum(e.debit for e in entries)
        total_paid = sum(e.credit for e in entries)

        return StudentStatement(
            student_id=student_id,
            student_name=f"{student.first_name} {student.last_name}",
            entries=entries,
            total_invoiced=total_invoiced,
            total_paid=total_paid,
            balance=total_invoiced - total_paid,
        )

    async def get_student_balance(
        self, db: AsyncSession, student_id: uuid.UUID
    ) -> dict:
        """Get current balance for a student."""
        tenant_id = get_tenant_id()

        total_q = select(func.coalesce(func.sum(BillingInvoice.total_amount), 0)).where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.student_id == student_id,
            BillingInvoice.deleted_at.is_(None),
            BillingInvoice.status != InvoiceStatus.CANCELLED.value,
        )
        total_invoiced = Decimal(str((await db.execute(total_q)).scalar() or 0))

        paid_q = select(func.coalesce(func.sum(BillingPayment.amount), 0)).where(
            BillingPayment.tenant_id == tenant_id,
            BillingPayment.student_id == student_id,
            BillingPayment.deleted_at.is_(None),
        )
        total_paid = Decimal(str((await db.execute(paid_q)).scalar() or 0))

        return {
            "student_id": student_id,
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "balance": total_invoiced - total_paid,
        }

    async def get_children_balances(
        self, db: AsyncSession, parent_id: uuid.UUID
    ) -> list[ChildBalance]:
        """Get balances for all children of a parent."""
        tenant_id = get_tenant_id()

        # Get children
        children_q = (
            select(Student)
            .join(ParentStudent, ParentStudent.student_id == Student.id)
            .where(
                ParentStudent.parent_id == parent_id,
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            )
        )
        children = list((await db.execute(children_q)).scalars().all())

        balances = []
        for child in children:
            bal = await self.get_student_balance(db, child.id)

            overdue_q = select(func.count()).select_from(
                select(BillingInvoice).where(
                    BillingInvoice.tenant_id == tenant_id,
                    BillingInvoice.student_id == child.id,
                    BillingInvoice.deleted_at.is_(None),
                    BillingInvoice.status == InvoiceStatus.OVERDUE.value,
                ).subquery()
            )
            overdue_count = (await db.execute(overdue_q)).scalar() or 0

            balances.append(
                ChildBalance(
                    student_id=child.id,
                    student_name=f"{child.first_name} {child.last_name}",
                    total_invoiced=bal["total_invoiced"],
                    total_paid=bal["total_paid"],
                    balance=bal["balance"],
                    overdue_count=overdue_count,
                )
            )

        return balances

    # =========================================================================
    # Summary
    # =========================================================================

    async def get_billing_summary(self, db: AsyncSession) -> BillingSummary:
        """Get billing dashboard summary stats."""
        tenant_id = get_tenant_id()

        base_filter = and_(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.deleted_at.is_(None),
            BillingInvoice.status != InvoiceStatus.CANCELLED.value,
        )

        total_invoiced = Decimal(
            str(
                (
                    await db.execute(
                        select(func.coalesce(func.sum(BillingInvoice.total_amount), 0)).where(base_filter)
                    )
                ).scalar()
                or 0
            )
        )

        total_collected = Decimal(
            str(
                (
                    await db.execute(
                        select(func.coalesce(func.sum(BillingPayment.amount), 0)).where(
                            BillingPayment.tenant_id == tenant_id,
                            BillingPayment.deleted_at.is_(None),
                        )
                    )
                ).scalar()
                or 0
            )
        )

        outstanding_q = select(
            func.coalesce(func.sum(BillingInvoice.balance), 0)
        ).where(
            base_filter,
            BillingInvoice.status.in_([
                InvoiceStatus.SENT.value,
                InvoiceStatus.PARTIALLY_PAID.value,
                InvoiceStatus.OVERDUE.value,
            ]),
        )
        total_outstanding = Decimal(str((await db.execute(outstanding_q)).scalar() or 0))

        overdue_q = select(
            func.coalesce(func.sum(BillingInvoice.balance), 0)
        ).where(
            base_filter,
            BillingInvoice.status == InvoiceStatus.OVERDUE.value,
        )
        total_overdue = Decimal(str((await db.execute(overdue_q)).scalar() or 0))

        invoice_count = (
            await db.execute(
                select(func.count()).select_from(
                    select(BillingInvoice).where(base_filter).subquery()
                )
            )
        ).scalar() or 0

        overdue_count = (
            await db.execute(
                select(func.count()).select_from(
                    select(BillingInvoice)
                    .where(base_filter, BillingInvoice.status == InvoiceStatus.OVERDUE.value)
                    .subquery()
                )
            )
        ).scalar() or 0

        payment_count = (
            await db.execute(
                select(func.count()).select_from(
                    select(BillingPayment)
                    .where(
                        BillingPayment.tenant_id == tenant_id,
                        BillingPayment.deleted_at.is_(None),
                    )
                    .subquery()
                )
            )
        ).scalar() or 0

        return BillingSummary(
            total_invoiced=total_invoiced,
            total_collected=total_collected,
            total_outstanding=total_outstanding,
            total_overdue=total_overdue,
            invoice_count=invoice_count,
            overdue_count=overdue_count,
            payment_count=payment_count,
        )

    # =========================================================================
    # Overdue Detection
    # =========================================================================

    async def check_overdue_invoices(self, db: AsyncSession) -> int:
        """Find and mark overdue invoices, notify parents. Returns count."""
        tenant_id = get_tenant_id()
        today = date.today()

        query = select(BillingInvoice).where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.deleted_at.is_(None),
            BillingInvoice.status.in_([
                InvoiceStatus.SENT.value,
                InvoiceStatus.PARTIALLY_PAID.value,
            ]),
            BillingInvoice.due_date < today,
        )
        result = await db.execute(query)
        overdue_invoices = list(result.scalars().unique().all())

        for invoice in overdue_invoices:
            invoice.status = InvoiceStatus.OVERDUE.value
            try:
                await self._notify_parents_overdue(db, invoice)
            except Exception:
                logger.exception("Failed to notify for overdue invoice %s", invoice.id)

        await db.flush()
        return len(overdue_invoices)

    # =========================================================================
    # Parent Access Helpers
    # =========================================================================

    async def get_parent_invoices(
        self,
        db: AsyncSession,
        parent_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[BillingInvoice], int]:
        """Get invoices for a parent's children (non-draft only)."""
        tenant_id = get_tenant_id()

        query = (
            select(BillingInvoice)
            .join(ParentStudent, BillingInvoice.student_id == ParentStudent.student_id)
            .where(
                BillingInvoice.tenant_id == tenant_id,
                BillingInvoice.deleted_at.is_(None),
                ParentStudent.parent_id == parent_id,
                BillingInvoice.status != InvoiceStatus.DRAFT.value,
            )
        )

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = query.order_by(BillingInvoice.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        return list(result.scalars().unique().all()), total

    async def verify_parent_access(
        self, db: AsyncSession, parent_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> bool:
        """Check if parent has access to an invoice (owns the student)."""
        invoice = await self.get_invoice(db, invoice_id)
        q = select(ParentStudent).where(
            ParentStudent.parent_id == parent_id,
            ParentStudent.student_id == invoice.student_id,
        )
        result = await db.execute(q)
        return result.scalar_one_or_none() is not None

    # =========================================================================
    # Notification Helpers
    # =========================================================================

    async def _get_parent_ids_for_student(
        self, db: AsyncSession, student_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Get parent user IDs for a student."""
        q = select(ParentStudent.parent_id).where(
            ParentStudent.student_id == student_id
        )
        result = await db.execute(q)
        return [row[0] for row in result.all()]

    async def _email_parents_invoice(
        self, db: AsyncSession, invoice: BillingInvoice
    ) -> None:
        """Send invoice email to all parents of the student."""
        from app.config import get_settings
        from app.models import Tenant
        from app.services.email_service import get_email_service

        app_settings = get_settings()
        email_service = get_email_service()

        # Get parent users
        parent_ids = await self._get_parent_ids_for_student(db, invoice.student_id)
        if not parent_ids:
            return

        parents_q = select(User).where(
            User.id.in_(parent_ids),
            User.is_active == True,
            User.deleted_at.is_(None),
        )
        parents = list((await db.execute(parents_q)).scalars().all())
        if not parents:
            return

        # Load tenant for contact info + billing settings
        tenant = await db.get(Tenant, invoice.tenant_id)
        if not tenant:
            return

        tenant_settings = tenant.settings or {}
        currency = tenant_settings.get("billing_currency", "ZAR")
        banking_details = tenant_settings.get("billing_banking_details", "") or None
        payment_instructions = tenant_settings.get("billing_payment_instructions", "") or None

        student_name = (
            f"{invoice.student.first_name} {invoice.student.last_name}"
            if invoice.student
            else "Student"
        )

        # Build line items from invoice items
        line_items = []
        for item in (invoice.items or []):
            line_items.append({
                "description": item.description,
                "quantity": item.quantity,
                "unit_amount": float(item.unit_amount),
                "total_amount": float(item.total_amount),
            })

        view_url = f"{app_settings.app_base_url}/billing"

        for parent in parents:
            try:
                await email_service.send_invoice_notification(
                    to=parent.email,
                    parent_name=parent.first_name,
                    student_name=student_name,
                    invoice_number=invoice.invoice_number,
                    total_amount=f"{invoice.total_amount:.2f}",
                    due_date=invoice.due_date.strftime("%d %b %Y"),
                    view_url=view_url,
                    tenant_name=tenant.name,
                    line_items=line_items,
                    currency=currency,
                    tenant_address=tenant.address,
                    tenant_phone=tenant.phone,
                    tenant_email=tenant.email,
                    banking_details=banking_details,
                    payment_instructions=payment_instructions,
                )
            except Exception:
                logger.exception(
                    "Failed to send invoice email to parent %s for invoice %s",
                    parent.id, invoice.id,
                )

    async def _notify_parents_invoice(
        self, db: AsyncSession, invoice: BillingInvoice
    ) -> None:
        """Send invoice notification to parents."""
        from app.services.notification_service import get_notification_service

        parent_ids = await self._get_parent_ids_for_student(db, invoice.student_id)
        if not parent_ids:
            return

        notification_service = get_notification_service()
        student_name = (
            f"{invoice.student.first_name} {invoice.student.last_name}"
            if invoice.student
            else "Student"
        )

        await notification_service.create_bulk_notifications(
            db=db,
            user_ids=parent_ids,
            title=f"Invoice: {invoice.invoice_number}",
            body=f"An invoice for {student_name} has been issued. Amount: {invoice.total_amount:.2f}",
            notification_type="INVOICE_SENT",
            reference_type="invoice",
            reference_id=invoice.id,
        )

    async def _notify_parents_payment(
        self, db: AsyncSession, payment: BillingPayment, invoice: BillingInvoice
    ) -> None:
        """Send payment confirmation notification to parents."""
        from app.services.notification_service import get_notification_service

        parent_ids = await self._get_parent_ids_for_student(db, invoice.student_id)
        if not parent_ids:
            return

        notification_service = get_notification_service()
        student_name = (
            f"{invoice.student.first_name} {invoice.student.last_name}"
            if invoice.student
            else "Student"
        )

        await notification_service.create_bulk_notifications(
            db=db,
            user_ids=parent_ids,
            title=f"Payment Received: {invoice.invoice_number}",
            body=f"A payment of {payment.amount:.2f} for {student_name} has been recorded. Remaining balance: {invoice.balance:.2f}",
            notification_type="PAYMENT_RECEIVED",
            reference_type="invoice",
            reference_id=invoice.id,
        )

    async def _notify_parents_overdue(
        self, db: AsyncSession, invoice: BillingInvoice
    ) -> None:
        """Send overdue notification to parents."""
        from app.services.notification_service import get_notification_service

        parent_ids = await self._get_parent_ids_for_student(db, invoice.student_id)
        if not parent_ids:
            return

        notification_service = get_notification_service()
        student_name = (
            f"{invoice.student.first_name} {invoice.student.last_name}"
            if invoice.student
            else "Student"
        )

        await notification_service.create_bulk_notifications(
            db=db,
            user_ids=parent_ids,
            title=f"Overdue: {invoice.invoice_number}",
            body=f"Invoice {invoice.invoice_number} for {student_name} is overdue. Outstanding balance: {invoice.balance:.2f}",
            notification_type="INVOICE_OVERDUE",
            reference_type="invoice",
            reference_id=invoice.id,
        )


# Singleton
_billing_service: BillingService | None = None


def get_billing_service() -> BillingService:
    """Get the billing service singleton."""
    global _billing_service
    if _billing_service is None:
        _billing_service = BillingService()
    return _billing_service
