"""Accounting module API."""

import datetime as _dt
import logging
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.services.accounting_service import get_accounting_service
from app.utils.permissions import require_role
from app.utils.tenant_context import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


async def _ensure_seeded(db: AsyncSession) -> None:
    """Backfill the chart of accounts for tenants that existed before the
    accounting module shipped. Idempotent — only runs if the tenant has zero
    accounts. Called on the first dashboard hit so the user sees a populated
    chart instead of an empty page on day one.
    """
    tenant_id = get_tenant_id()
    if not tenant_id:
        return
    service = get_accounting_service()
    existing = await service.list_chart_accounts(db, active_only=False)
    if existing:
        return  # Already seeded
    try:
        await service.seed_defaults_for_tenant(db, tenant_id)
        await db.commit()
        logger.info(f"Backfilled accounting defaults for tenant {tenant_id}")
    except Exception:
        logger.exception("Accounting backfill failed (non-fatal)")
        await db.rollback()


# ──────────────── Schemas ────────────────

class ChartAccountIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=120)
    type: str = Field(..., description="INCOME | EXPENSE | ASSET | LIABILITY")
    description: str | None = None


class ChartAccountUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class BankAccountIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    bank_name: str | None = None
    account_number: str | None = None
    branch_code: str | None = None
    account_type: str = Field("OPERATING")
    currency: str = Field("ZAR")
    opening_balance: Decimal = Field(default=Decimal("0"))
    opening_balance_date: date | None = None
    is_default: bool = False
    notes: str | None = None


class BankAccountUpdate(BaseModel):
    name: str | None = None
    bank_name: str | None = None
    account_number: str | None = None
    branch_code: str | None = None
    account_type: str | None = None
    currency: str | None = None
    opening_balance: Decimal | None = None
    opening_balance_date: date | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    notes: str | None = None


class VendorIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    vat_number: str | None = None
    banking_details: str | None = None
    notes: str | None = None


class VendorUpdate(BaseModel):
    name: str | None = None
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    vat_number: str | None = None
    banking_details: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class ExpenseIn(BaseModel):
    date: date
    amount: Decimal = Field(..., gt=0)
    account_id: uuid.UUID
    bank_account_id: uuid.UUID
    vendor_id: uuid.UUID | None = None
    description: str | None = None
    reference: str | None = None
    vat_amount: Decimal | None = None
    vat_rate: Decimal | None = None
    receipt_file_id: uuid.UUID | None = None


class IncomeIn(BaseModel):
    date: date
    amount: Decimal = Field(..., gt=0)
    account_id: uuid.UUID
    bank_account_id: uuid.UUID
    student_id: uuid.UUID | None = None
    description: str | None = None
    reference: str | None = None
    vat_amount: Decimal | None = None
    vat_rate: Decimal | None = None
    receipt_file_id: uuid.UUID | None = None


class TransferIn(BaseModel):
    date: date
    amount: Decimal = Field(..., gt=0)
    from_bank_id: uuid.UUID
    to_bank_id: uuid.UUID
    description: str | None = None
    reference: str | None = None


class TransactionUpdate(BaseModel):
    date: _dt.date | None = None
    amount: Decimal | None = None
    account_id: uuid.UUID | None = None
    bank_account_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None
    student_id: uuid.UUID | None = None
    description: str | None = None
    reference: str | None = None
    vat_amount: Decimal | None = None
    vat_rate: Decimal | None = None
    receipt_file_id: uuid.UUID | None = None


# ──────────────── Serializers ────────────────

def _account_dict(a) -> dict:
    return {
        "id": str(a.id),
        "code": a.code,
        "name": a.name,
        "type": a.type,
        "description": a.description,
        "is_active": a.is_active,
        "is_system": a.is_system,
        "display_order": a.display_order,
    }


def _bank_dict(b, balance: Decimal | None = None) -> dict:
    return {
        "id": str(b.id),
        "name": b.name,
        "bank_name": b.bank_name,
        "account_number": b.account_number,
        "branch_code": b.branch_code,
        "account_type": b.account_type,
        "currency": b.currency,
        "opening_balance": float(b.opening_balance or 0),
        "opening_balance_date": b.opening_balance_date.isoformat() if b.opening_balance_date else None,
        "is_default": b.is_default,
        "is_active": b.is_active,
        "notes": b.notes,
        "balance": float(balance) if balance is not None else None,
    }


def _vendor_dict(v) -> dict:
    return {
        "id": str(v.id),
        "name": v.name,
        "contact_person": v.contact_person,
        "email": v.email,
        "phone": v.phone,
        "address": v.address,
        "vat_number": v.vat_number,
        "banking_details": v.banking_details,
        "notes": v.notes,
        "is_active": v.is_active,
    }


def _tx_dict(tx) -> dict:
    return {
        "id": str(tx.id),
        "date": tx.date.isoformat() if tx.date else None,
        "type": tx.type,
        "amount": float(tx.amount),
        "currency": tx.currency,
        "account_id": str(tx.account_id) if tx.account_id else None,
        "account_code": tx.account.code if tx.account else None,
        "account_name": tx.account.name if tx.account else None,
        "bank_account_id": str(tx.bank_account_id) if tx.bank_account_id else None,
        "bank_account_name": tx.bank_account.name if tx.bank_account else None,
        "transfer_to_bank_account_id": (
            str(tx.transfer_to_bank_account_id) if tx.transfer_to_bank_account_id else None
        ),
        "transfer_to_bank_account_name": (
            tx.transfer_to_bank_account.name if tx.transfer_to_bank_account else None
        ),
        "vendor_id": str(tx.vendor_id) if tx.vendor_id else None,
        "vendor_name": tx.vendor.name if tx.vendor else None,
        "student_id": str(tx.student_id) if tx.student_id else None,
        "student_name": (
            f"{tx.student.first_name} {tx.student.last_name}" if tx.student else None
        ),
        "billing_payment_id": str(tx.billing_payment_id) if tx.billing_payment_id else None,
        "is_auto_linked": tx.billing_payment_id is not None,
        "description": tx.description,
        "reference": tx.reference,
        "vat_amount": float(tx.vat_amount) if tx.vat_amount is not None else None,
        "vat_rate": float(tx.vat_rate) if tx.vat_rate is not None else None,
        "receipt_file_id": str(tx.receipt_file_id) if tx.receipt_file_id else None,
        "created_by": str(tx.created_by) if tx.created_by else None,
        "created_by_name": (
            f"{tx.created_by_user.first_name} {tx.created_by_user.last_name}"
            if tx.created_by_user else None
        ),
    }


# ──────────────── Chart of accounts ────────────────

@router.get("/accounts", response_model=APIResponse[list[dict]])
@require_role(Role.SCHOOL_ADMIN)
async def list_accounts(
    type: str | None = None,
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
):
    items = await get_accounting_service().list_chart_accounts(db, type_filter=type, active_only=active_only)
    return APIResponse(data=[_account_dict(a) for a in items])


@router.post("/accounts", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def create_account(body: ChartAccountIn, db: AsyncSession = Depends(get_db)):
    a = await get_accounting_service().create_chart_account(
        db, code=body.code, name=body.name, type_=body.type, description=body.description
    )
    await db.commit()
    return APIResponse(data=_account_dict(a), message="Account created")


@router.put("/accounts/{account_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def update_account(
    account_id: uuid.UUID, body: ChartAccountUpdate, db: AsyncSession = Depends(get_db),
):
    a = await get_accounting_service().update_chart_account(
        db, account_id, name=body.name, description=body.description, is_active=body.is_active
    )
    await db.commit()
    return APIResponse(data=_account_dict(a), message="Account updated")


@router.delete("/accounts/{account_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def delete_account(account_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await get_accounting_service().delete_chart_account(db, account_id)
    await db.commit()
    return APIResponse(data={"deleted": True}, message="Account deleted")


# ──────────────── Bank accounts ────────────────

@router.get("/banks", response_model=APIResponse[list[dict]])
@require_role(Role.SCHOOL_ADMIN)
async def list_banks(
    active_only: bool = True,
    with_balance: bool = False,
    db: AsyncSession = Depends(get_db),
):
    service = get_accounting_service()
    banks = await service.list_bank_accounts(db, active_only=active_only)
    if with_balance:
        out = []
        for b in banks:
            bal = await service.get_bank_balance(db, b.id)
            out.append(_bank_dict(b, bal))
        return APIResponse(data=out)
    return APIResponse(data=[_bank_dict(b) for b in banks])


@router.post("/banks", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def create_bank(body: BankAccountIn, db: AsyncSession = Depends(get_db)):
    b = await get_accounting_service().create_bank_account(db, **body.model_dump())
    await db.commit()
    return APIResponse(data=_bank_dict(b), message="Bank account created")


@router.put("/banks/{bank_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def update_bank(
    bank_id: uuid.UUID, body: BankAccountUpdate, db: AsyncSession = Depends(get_db),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    b = await get_accounting_service().update_bank_account(db, bank_id, **fields)
    await db.commit()
    return APIResponse(data=_bank_dict(b), message="Bank account updated")


@router.delete("/banks/{bank_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def delete_bank(bank_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await get_accounting_service().delete_bank_account(db, bank_id)
    await db.commit()
    return APIResponse(data={"deleted": True}, message="Bank account deactivated")


# ──────────────── Vendors ────────────────

@router.get("/vendors", response_model=APIResponse[list[dict]])
@require_role(Role.SCHOOL_ADMIN)
async def list_vendors(
    active_only: bool = True,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    items = await get_accounting_service().list_vendors(db, active_only=active_only, search=search)
    return APIResponse(data=[_vendor_dict(v) for v in items])


@router.post("/vendors", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def create_vendor(body: VendorIn, db: AsyncSession = Depends(get_db)):
    v = await get_accounting_service().create_vendor(db, **body.model_dump())
    await db.commit()
    return APIResponse(data=_vendor_dict(v), message="Vendor created")


@router.put("/vendors/{vendor_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def update_vendor(
    vendor_id: uuid.UUID, body: VendorUpdate, db: AsyncSession = Depends(get_db),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    v = await get_accounting_service().update_vendor(db, vendor_id, **fields)
    await db.commit()
    return APIResponse(data=_vendor_dict(v), message="Vendor updated")


@router.delete("/vendors/{vendor_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def delete_vendor(vendor_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await get_accounting_service().delete_vendor(db, vendor_id)
    await db.commit()
    return APIResponse(data={"deleted": True}, message="Vendor deactivated")


# ──────────────── Transactions ────────────────

@router.get("/transactions", response_model=APIResponse[list[dict]])
@require_role(Role.SCHOOL_ADMIN)
async def list_transactions(
    type: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    bank_account_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    items, total = await get_accounting_service().list_transactions(
        db, type_filter=type, from_date=from_date, to_date=to_date,
        bank_account_id=bank_account_id, account_id=account_id,
        vendor_id=vendor_id, search=search, page=page, page_size=page_size,
    )
    total_pages = (total + page_size - 1) // page_size
    return APIResponse(
        data=[_tx_dict(t) for t in items],
        pagination=PaginationMeta(
            page=page, page_size=page_size, total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages, has_prev=page > 1,
        ),
    )


@router.post("/expenses", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def record_expense(body: ExpenseIn, db: AsyncSession = Depends(get_db)):
    tx = await get_accounting_service().record_expense(
        db,
        date_=body.date, amount=body.amount,
        account_id=body.account_id, bank_account_id=body.bank_account_id,
        vendor_id=body.vendor_id, description=body.description,
        reference=body.reference, vat_amount=body.vat_amount, vat_rate=body.vat_rate,
        receipt_file_id=body.receipt_file_id,
    )
    await db.commit()
    return APIResponse(data=_tx_dict(tx), message="Expense recorded")


@router.post("/income", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def record_income(body: IncomeIn, db: AsyncSession = Depends(get_db)):
    tx = await get_accounting_service().record_income(
        db,
        date_=body.date, amount=body.amount,
        account_id=body.account_id, bank_account_id=body.bank_account_id,
        student_id=body.student_id, description=body.description,
        reference=body.reference, vat_amount=body.vat_amount, vat_rate=body.vat_rate,
        receipt_file_id=body.receipt_file_id,
    )
    await db.commit()
    return APIResponse(data=_tx_dict(tx), message="Income recorded")


@router.post("/transfers", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def record_transfer(body: TransferIn, db: AsyncSession = Depends(get_db)):
    tx = await get_accounting_service().record_transfer(
        db,
        date_=body.date, amount=body.amount,
        from_bank_id=body.from_bank_id, to_bank_id=body.to_bank_id,
        description=body.description, reference=body.reference,
    )
    await db.commit()
    return APIResponse(data=_tx_dict(tx), message="Transfer recorded")


@router.put("/transactions/{tx_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def update_transaction(
    tx_id: uuid.UUID, body: TransactionUpdate, db: AsyncSession = Depends(get_db),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    tx = await get_accounting_service().update_transaction(db, tx_id, **fields)
    await db.commit()
    return APIResponse(data=_tx_dict(tx), message="Transaction updated")


@router.delete("/transactions/{tx_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def delete_transaction(tx_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await get_accounting_service().delete_transaction(db, tx_id)
    await db.commit()
    return APIResponse(data={"deleted": True}, message="Transaction deleted")


# ──────────────── Reports ────────────────

@router.get("/dashboard", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def dashboard_summary(db: AsyncSession = Depends(get_db)):
    """This-month income/expense/net + cash position."""
    # Backfill the default chart of accounts + Operating bank for tenants that
    # existed before the accounting module shipped. No-op if already seeded.
    await _ensure_seeded(db)
    summary = await get_accounting_service().dashboard_summary(db)
    return APIResponse(
        data={
            **summary,
            "income_this_month": float(summary["income_this_month"]),
            "expense_this_month": float(summary["expense_this_month"]),
            "net_this_month": float(summary["net_this_month"]),
            "cash_total": float(summary["cash_total"]),
            "banks": [
                {**b, "balance": float(b["balance"])} for b in summary["banks"]
            ],
        }
    )


@router.get("/reports/profit-loss", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def profit_loss(
    from_date: date,
    to_date: date,
    db: AsyncSession = Depends(get_db),
):
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from_date must be ≤ to_date")
    pl = await get_accounting_service().profit_and_loss(db, from_date, to_date)
    # Cast Decimals to floats for JSON
    pl["income"] = [{**r, "total": float(r["total"])} for r in pl["income"]]
    pl["expense"] = [{**r, "total": float(r["total"])} for r in pl["expense"]]
    pl["income_total"] = float(pl["income_total"])
    pl["expense_total"] = float(pl["expense_total"])
    pl["net_profit"] = float(pl["net_profit"])
    return APIResponse(data=pl)


@router.get("/reports/expenses-by-category", response_model=APIResponse[list[dict]])
@require_role(Role.SCHOOL_ADMIN)
async def expenses_by_category(
    from_date: date,
    to_date: date,
    db: AsyncSession = Depends(get_db),
):
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from_date must be ≤ to_date")
    rows = await get_accounting_service().expenses_by_category(db, from_date, to_date)
    rows = [{**r, "total": float(r["total"])} for r in rows]
    return APIResponse(data=rows)


@router.get("/reports/cash-position", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def cash_position(
    as_of: date | None = None,
    db: AsyncSession = Depends(get_db),
):
    cp = await get_accounting_service().cash_position(db, as_of=as_of)
    cp["total"] = float(cp["total"])
    cp["banks"] = [{**b, "balance": float(b["balance"])} for b in cp["banks"]]
    return APIResponse(data=cp)
