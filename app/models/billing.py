"""Billing models for fee management, invoicing, and payments."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, TenantScopedModel, TimestampMixin


class FeeFrequency(str, Enum):
    """How often a fee is charged."""

    MONTHLY = "MONTHLY"
    TERMLY = "TERMLY"
    ANNUALLY = "ANNUALLY"
    ONCE_OFF = "ONCE_OFF"


class FeeAppliesTo(str, Enum):
    """Scope of a fee item."""

    ALL = "ALL"
    CLASS = "CLASS"


class InvoiceStatus(str, Enum):
    """Status of an invoice."""

    DRAFT = "DRAFT"
    SENT = "SENT"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"


class PaymentMethod(str, Enum):
    """Method of payment."""

    CASH = "CASH"
    BANK_TRANSFER = "BANK_TRANSFER"
    EFT = "EFT"
    CARD = "CARD"
    CHEQUE = "CHEQUE"
    OTHER = "OTHER"


class BillingFeeItem(TenantScopedModel):
    """A fee item that can be charged to students (e.g. Tuition, Book Fee)."""

    __tablename__ = "billing_fee_items"
    __table_args__ = (
        Index(
            "idx_billing_fee_items_tenant_active",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL AND is_active = true"),
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    frequency: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FeeFrequency.MONTHLY.value
    )
    applies_to: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FeeAppliesTo.ALL.value
    )
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    school_class = relationship("SchoolClass", lazy="selectin")


class BillingInvoice(TenantScopedModel):
    """An invoice issued to a student's parents."""

    __tablename__ = "billing_invoices"
    __table_args__ = (
        Index(
            "idx_billing_invoices_tenant_status",
            "tenant_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_billing_invoices_student",
            "tenant_id",
            "student_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_billing_invoices_number",
            "tenant_id",
            "invoice_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_number: Mapped[str] = mapped_column(String(30), nullable=False)
    billing_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    billing_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=InvoiceStatus.DRAFT.value
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    student = relationship("Student", lazy="selectin")
    created_by_user = relationship("User", lazy="selectin")
    items = relationship(
        "BillingInvoiceItem",
        back_populates="invoice",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    payments = relationship(
        "BillingPayment",
        back_populates="invoice",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class BillingInvoiceItem(BaseModel):
    """A line item on an invoice."""

    __tablename__ = "billing_invoice_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fee_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_fee_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # Relationships
    invoice = relationship("BillingInvoice", back_populates="items")
    fee_item = relationship("BillingFeeItem", lazy="selectin")


class BillingPayment(TenantScopedModel):
    """A payment recorded against an invoice."""

    __tablename__ = "billing_payments"
    __table_args__ = (
        Index(
            "idx_billing_payments_invoice",
            "invoice_id",
        ),
        Index(
            "idx_billing_payments_student",
            "tenant_id",
            "student_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PaymentMethod.CASH.value
    )
    reference_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationships
    invoice = relationship("BillingInvoice", back_populates="payments")
    student = relationship("Student", lazy="selectin")
    recorded_by_user = relationship("User", lazy="selectin")
