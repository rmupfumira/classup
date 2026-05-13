"""Accounting models — chart of accounts, banks, vendors, and transactions.

Design: single-entry accounting. Each row in `accounting_transactions` is one
money movement (income, expense, or transfer). Sufficient for small/medium
schools with turnover up to ~R10m.

Auto-linkage: when a BillingPayment is recorded the AccountingService creates
a matching INCOME transaction so the P&L report picks it up automatically.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScopedModel, TimestampMixin, SoftDeleteMixin


class AccountType(str, Enum):
    """Top-level account categories.

    Five buckets, matching the standard accounting equation:
        Assets = Liabilities + Equity
        Equity = Capital + Retained (Income − Expense)
    EQUITY covers both for-profit (Owner's Capital, Retained Earnings) and
    non-profit (Net Assets — Unrestricted / Restricted) bookkeeping models.
    """

    INCOME = "INCOME"
    EXPENSE = "EXPENSE"
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"


class TransactionType(str, Enum):
    """A money movement is one of these three."""

    INCOME = "INCOME"      # Money received
    EXPENSE = "EXPENSE"    # Money paid out
    TRANSFER = "TRANSFER"  # Between own bank accounts (no P&L impact)


class BankAccountType(str, Enum):
    OPERATING = "OPERATING"
    SAVINGS = "SAVINGS"
    PETTY_CASH = "PETTY_CASH"
    OTHER = "OTHER"


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------

class ChartAccount(TenantScopedModel):
    """A single line in the school's Chart of Accounts.

    Examples:
        INCOME / Tuition Fees
        EXPENSE / Salaries & Wages
        ASSET / Petty Cash
        LIABILITY / Loans
    """

    __tablename__ = "chart_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_chart_accounts_tenant_code"),
        Index("idx_chart_accounts_tenant_type", "tenant_id", "type"),
    )

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Marks accounts the system uses for auto-linkage (e.g. tuition payments
    # default to "Tuition Fees"). We don't allow deleting these.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


# ---------------------------------------------------------------------------
# Bank accounts
# ---------------------------------------------------------------------------

class BankAccount(TenantScopedModel):
    """A school bank account or cash float."""

    __tablename__ = "bank_accounts"
    __table_args__ = (
        Index("idx_bank_accounts_tenant_active", "tenant_id", "is_active"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    branch_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    account_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BankAccountType.OPERATING.value,
        server_default=BankAccountType.OPERATING.value,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="ZAR", server_default="ZAR")
    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), nullable=False, default=0,
        server_default="0",
    )
    opening_balance_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Vendors / Suppliers
# ---------------------------------------------------------------------------

class Vendor(TenantScopedModel):
    """A supplier the school pays — e.g. cleaning company, stationery shop."""

    __tablename__ = "vendors"
    __table_args__ = (
        Index("idx_vendors_tenant_active", "tenant_id", "is_active"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_person: Mapped[str | None] = mapped_column(String(150), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    vat_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    banking_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


# ---------------------------------------------------------------------------
# Transactions (the heart of the system)
# ---------------------------------------------------------------------------

class AccountingTransaction(TenantScopedModel):
    """A single money movement.

    For an INCOME row: amount is positive money received, hits ChartAccount of
    type INCOME, credits the bank_account.
    For an EXPENSE row: amount is positive money paid out, hits ChartAccount of
    type EXPENSE, debits the bank_account.
    For a TRANSFER row: amount moves from `bank_account_id` to
    `transfer_to_bank_account_id`. Both must be set; chart account is null.
    """

    __tablename__ = "accounting_transactions"
    __table_args__ = (
        Index("idx_accounting_tx_tenant_date", "tenant_id", "date"),
        Index("idx_accounting_tx_account", "account_id"),
        Index("idx_accounting_tx_bank", "bank_account_id"),
        Index("idx_accounting_tx_billing_payment", "billing_payment_id"),
    )

    date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)

    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="ZAR", server_default="ZAR")

    # For INCOME / EXPENSE: which P&L category. NULL for transfers.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chart_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The bank account this hits.
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # For TRANSFER only — the destination bank.
    transfer_to_bank_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Optional vendor (for expenses)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional student linkage (for income — links a payment to a student)
    student_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional billing payment linkage (auto-created when a payment is recorded)
    billing_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_payments.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,  # one accounting transaction per billing payment
    )

    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # VAT — optional. amount above is INCLUSIVE of VAT.
    vat_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    vat_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=6, scale=4), nullable=True
    )

    receipt_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("file_entities.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships (lazy="selectin" so list views can show names without N+1)
    account = relationship("ChartAccount", lazy="selectin", foreign_keys=[account_id])
    bank_account = relationship("BankAccount", lazy="selectin", foreign_keys=[bank_account_id])
    transfer_to_bank_account = relationship(
        "BankAccount", lazy="selectin", foreign_keys=[transfer_to_bank_account_id]
    )
    vendor = relationship("Vendor", lazy="selectin")
    student = relationship("Student", lazy="selectin")
    billing_payment = relationship("BillingPayment", lazy="selectin")
    receipt_file = relationship("FileEntity", lazy="selectin")
    created_by_user = relationship("User", lazy="selectin", foreign_keys=[created_by])
