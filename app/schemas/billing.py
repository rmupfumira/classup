"""Pydantic schemas for billing module."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# --- Enums ---

class FeeFrequency(str, Enum):
    MONTHLY = "MONTHLY"
    TERMLY = "TERMLY"
    ANNUALLY = "ANNUALLY"
    ONCE_OFF = "ONCE_OFF"


class FeeAppliesTo(str, Enum):
    ALL = "ALL"
    CLASS = "CLASS"


class InvoiceStatus(str, Enum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"


class PaymentMethod(str, Enum):
    CASH = "CASH"
    BANK_TRANSFER = "BANK_TRANSFER"
    EFT = "EFT"
    CARD = "CARD"
    CHEQUE = "CHEQUE"
    OTHER = "OTHER"


# --- Fee Items ---

class FeeItemCreate(BaseModel):
    name: str = Field(..., max_length=255)
    description: str | None = None
    amount: Decimal = Field(..., ge=0)
    frequency: FeeFrequency = FeeFrequency.MONTHLY
    applies_to: FeeAppliesTo = FeeAppliesTo.ALL
    class_id: uuid.UUID | None = None
    is_active: bool = True
    display_order: int = 0

    model_config = ConfigDict(str_strip_whitespace=True)


class FeeItemUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    amount: Decimal | None = Field(None, ge=0)
    frequency: FeeFrequency | None = None
    applies_to: FeeAppliesTo | None = None
    class_id: uuid.UUID | None = None
    is_active: bool | None = None
    display_order: int | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class FeeItemResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    amount: Decimal
    frequency: str
    applies_to: str
    class_id: uuid.UUID | None
    class_name: str | None = None
    is_active: bool
    display_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Invoice Items ---

class InvoiceItemCreate(BaseModel):
    fee_item_id: uuid.UUID | None = None
    description: str = Field(..., max_length=500)
    quantity: int = Field(1, ge=1)
    unit_amount: Decimal = Field(..., ge=0)


class InvoiceItemResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    fee_item_id: uuid.UUID | None
    description: str
    quantity: int
    unit_amount: Decimal
    total_amount: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Invoices ---

class InvoiceCreate(BaseModel):
    student_id: uuid.UUID
    billing_period_start: date | None = None
    billing_period_end: date | None = None
    due_date: date
    notes: str | None = None
    items: list[InvoiceItemCreate] = []

    model_config = ConfigDict(str_strip_whitespace=True)


class InvoiceUpdate(BaseModel):
    billing_period_start: date | None = None
    billing_period_end: date | None = None
    due_date: date | None = None
    notes: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class InvoiceResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str | None = None
    class_name: str | None = None
    invoice_number: str
    billing_period_start: date | None
    billing_period_end: date | None
    due_date: date
    subtotal: Decimal
    total_amount: Decimal
    amount_paid: Decimal
    balance: Decimal
    status: str
    notes: str | None
    created_by: uuid.UUID
    created_by_name: str | None = None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[InvoiceItemResponse] = []
    payments: list["PaymentResponse"] = []

    model_config = ConfigDict(from_attributes=True)


class GenerateInvoicesRequest(BaseModel):
    class_id: uuid.UUID
    student_ids: list[uuid.UUID]
    fee_item_ids: list[uuid.UUID]
    billing_period_start: date | None = None
    billing_period_end: date | None = None
    due_date: date

    model_config = ConfigDict(str_strip_whitespace=True)


class GenerateInvoicesResponse(BaseModel):
    invoices_created: int
    invoice_ids: list[uuid.UUID]


# --- Payments ---

class PaymentCreate(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal = Field(..., gt=0)
    payment_method: PaymentMethod = PaymentMethod.CASH
    reference_number: str | None = Field(None, max_length=100)
    payment_date: date
    notes: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class PaymentResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    invoice_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str | None = None
    invoice_number: str | None = None
    amount: Decimal
    payment_method: str
    reference_number: str | None
    payment_date: date
    notes: str | None
    recorded_by: uuid.UUID
    recorded_by_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Statement ---

class StatementEntry(BaseModel):
    date: date
    type: str  # "INVOICE" or "PAYMENT"
    description: str
    reference: str  # invoice_number or payment reference
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")
    running_balance: Decimal = Decimal("0.00")
    entity_id: uuid.UUID


class StudentStatement(BaseModel):
    student_id: uuid.UUID
    student_name: str
    entries: list[StatementEntry]
    total_invoiced: Decimal
    total_paid: Decimal
    balance: Decimal


# --- Summary ---

class BillingSummary(BaseModel):
    total_invoiced: Decimal
    total_collected: Decimal
    total_outstanding: Decimal
    total_overdue: Decimal
    invoice_count: int
    overdue_count: int
    payment_count: int


class ChildBalance(BaseModel):
    student_id: uuid.UUID
    student_name: str
    total_invoiced: Decimal
    total_paid: Decimal
    balance: Decimal
    overdue_count: int
