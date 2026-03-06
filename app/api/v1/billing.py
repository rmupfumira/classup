"""Billing API endpoints."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.billing import (
    BillingSummary,
    ChildBalance,
    FeeItemCreate,
    FeeItemResponse,
    FeeItemUpdate,
    GenerateInvoicesRequest,
    GenerateInvoicesResponse,
    InvoiceCreate,
    InvoiceResponse,
    InvoiceUpdate,
    PaymentCreate,
    PaymentResponse,
    StudentStatement,
)
from app.schemas.common import APIResponse, PaginationMeta
from app.services.billing_service import get_billing_service
from app.utils.permissions import require_role
from app.utils.tenant_context import get_current_user_id, get_current_user_role

router = APIRouter()


# =========================================================================
# Response Builders
# =========================================================================

def _build_fee_item_response(item) -> FeeItemResponse:
    return FeeItemResponse(
        id=item.id,
        tenant_id=item.tenant_id,
        name=item.name,
        description=item.description,
        amount=item.amount,
        frequency=item.frequency,
        applies_to=item.applies_to,
        class_id=item.class_id,
        class_name=item.school_class.name if item.school_class else None,
        is_active=item.is_active,
        display_order=item.display_order,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _build_invoice_response(invoice) -> InvoiceResponse:
    return InvoiceResponse(
        id=invoice.id,
        tenant_id=invoice.tenant_id,
        student_id=invoice.student_id,
        student_name=(
            f"{invoice.student.first_name} {invoice.student.last_name}"
            if invoice.student
            else None
        ),
        class_name=(
            invoice.student.school_class.name
            if invoice.student and invoice.student.school_class
            else None
        ),
        invoice_number=invoice.invoice_number,
        billing_period_start=invoice.billing_period_start,
        billing_period_end=invoice.billing_period_end,
        due_date=invoice.due_date,
        subtotal=invoice.subtotal,
        total_amount=invoice.total_amount,
        amount_paid=invoice.amount_paid,
        balance=invoice.balance,
        status=invoice.status,
        notes=invoice.notes,
        created_by=invoice.created_by,
        created_by_name=(
            f"{invoice.created_by_user.first_name} {invoice.created_by_user.last_name}"
            if invoice.created_by_user
            else None
        ),
        sent_at=invoice.sent_at,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
        items=[
            {
                "id": li.id,
                "invoice_id": li.invoice_id,
                "fee_item_id": li.fee_item_id,
                "description": li.description,
                "quantity": li.quantity,
                "unit_amount": li.unit_amount,
                "total_amount": li.total_amount,
                "created_at": li.created_at,
            }
            for li in (invoice.items or [])
        ],
        payments=[
            _build_payment_response(p) for p in (invoice.payments or [])
            if p.deleted_at is None
        ],
    )


def _build_payment_response(payment) -> PaymentResponse:
    return PaymentResponse(
        id=payment.id,
        tenant_id=payment.tenant_id,
        invoice_id=payment.invoice_id,
        student_id=payment.student_id,
        student_name=(
            f"{payment.student.first_name} {payment.student.last_name}"
            if payment.student
            else None
        ),
        invoice_number=(
            payment.invoice.invoice_number if payment.invoice else None
        ),
        amount=payment.amount,
        payment_method=payment.payment_method,
        reference_number=payment.reference_number,
        payment_date=payment.payment_date,
        notes=payment.notes,
        recorded_by=payment.recorded_by,
        recorded_by_name=(
            f"{payment.recorded_by_user.first_name} {payment.recorded_by_user.last_name}"
            if payment.recorded_by_user
            else None
        ),
        created_at=payment.created_at,
        updated_at=payment.updated_at,
    )


# =========================================================================
# Fee Items
# =========================================================================

@router.get("/fee-items", response_model=APIResponse[list[FeeItemResponse]])
@require_role(Role.SCHOOL_ADMIN)
async def list_fee_items(
    is_active: bool | None = Query(None),
    class_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List fee items."""
    service = get_billing_service()
    items, total = await service.get_fee_items(
        db, is_active=is_active, class_id=class_id, page=page, page_size=page_size
    )
    total_pages = (total + page_size - 1) // page_size
    return APIResponse(
        data=[_build_fee_item_response(i) for i in items],
        pagination=PaginationMeta(
            page=page, page_size=page_size, total_items=total,
            total_pages=total_pages, has_next=page < total_pages, has_prev=page > 1,
        ),
    )


@router.post("/fee-items", response_model=APIResponse[FeeItemResponse])
@require_role(Role.SCHOOL_ADMIN)
async def create_fee_item(
    data: FeeItemCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a fee item."""
    service = get_billing_service()
    item = await service.create_fee_item(db, data)
    return APIResponse(data=_build_fee_item_response(item), message="Fee item created")


@router.get("/fee-items/{fee_item_id}", response_model=APIResponse[FeeItemResponse])
@require_role(Role.SCHOOL_ADMIN)
async def get_fee_item(
    fee_item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a fee item."""
    service = get_billing_service()
    item = await service.get_fee_item(db, fee_item_id)
    return APIResponse(data=_build_fee_item_response(item))


@router.put("/fee-items/{fee_item_id}", response_model=APIResponse[FeeItemResponse])
@require_role(Role.SCHOOL_ADMIN)
async def update_fee_item(
    fee_item_id: uuid.UUID,
    data: FeeItemUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a fee item."""
    service = get_billing_service()
    item = await service.update_fee_item(db, fee_item_id, data)
    return APIResponse(data=_build_fee_item_response(item), message="Fee item updated")


@router.delete("/fee-items/{fee_item_id}", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN)
async def delete_fee_item(
    fee_item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a fee item."""
    service = get_billing_service()
    await service.delete_fee_item(db, fee_item_id)
    return APIResponse(message="Fee item deleted")


# =========================================================================
# Invoices
# =========================================================================

@router.get("/invoices", response_model=APIResponse[list[InvoiceResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.PARENT)
async def list_invoices(
    student_id: uuid.UUID | None = Query(None),
    class_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List invoices. Parents see only their children's non-draft invoices."""
    service = get_billing_service()
    role = get_current_user_role()

    if role == Role.PARENT.value:
        parent_id = get_current_user_id()
        invoices, total = await service.get_parent_invoices(
            db, parent_id, page=page, page_size=page_size
        )
    else:
        invoices, total = await service.get_invoices(
            db, student_id=student_id, class_id=class_id, status=status,
            page=page, page_size=page_size,
        )

    total_pages = (total + page_size - 1) // page_size
    return APIResponse(
        data=[_build_invoice_response(i) for i in invoices],
        pagination=PaginationMeta(
            page=page, page_size=page_size, total_items=total,
            total_pages=total_pages, has_next=page < total_pages, has_prev=page > 1,
        ),
    )


@router.post("/invoices", response_model=APIResponse[InvoiceResponse])
@require_role(Role.SCHOOL_ADMIN)
async def create_invoice(
    data: InvoiceCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a single invoice."""
    service = get_billing_service()
    invoice = await service.create_invoice(db, data)
    return APIResponse(data=_build_invoice_response(invoice), message="Invoice created")


@router.post("/invoices/generate", response_model=APIResponse[GenerateInvoicesResponse])
@require_role(Role.SCHOOL_ADMIN)
async def generate_invoices(
    data: GenerateInvoicesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Batch generate invoices for students."""
    service = get_billing_service()
    invoices = await service.generate_invoices(db, data)
    return APIResponse(
        data=GenerateInvoicesResponse(
            invoices_created=len(invoices),
            invoice_ids=[i.id for i in invoices],
        ),
        message=f"{len(invoices)} invoices generated",
    )


@router.get("/invoices/{invoice_id}", response_model=APIResponse[InvoiceResponse])
@require_role(Role.SCHOOL_ADMIN, Role.PARENT)
async def get_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get invoice detail."""
    service = get_billing_service()
    role = get_current_user_role()

    if role == Role.PARENT.value:
        parent_id = get_current_user_id()
        has_access = await service.verify_parent_access(db, parent_id, invoice_id)
        if not has_access:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Access denied")

    invoice = await service.get_invoice(db, invoice_id)
    return APIResponse(data=_build_invoice_response(invoice))


@router.put("/invoices/{invoice_id}", response_model=APIResponse[InvoiceResponse])
@require_role(Role.SCHOOL_ADMIN)
async def update_invoice(
    invoice_id: uuid.UUID,
    data: InvoiceUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a draft invoice."""
    service = get_billing_service()
    invoice = await service.update_invoice(db, invoice_id, data)
    return APIResponse(data=_build_invoice_response(invoice), message="Invoice updated")


@router.delete("/invoices/{invoice_id}", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN)
async def delete_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a draft invoice."""
    service = get_billing_service()
    await service.delete_invoice(db, invoice_id)
    return APIResponse(message="Invoice deleted")


@router.post("/invoices/{invoice_id}/send", response_model=APIResponse[InvoiceResponse])
@require_role(Role.SCHOOL_ADMIN)
async def send_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Send invoice to parents."""
    service = get_billing_service()
    invoice = await service.send_invoice(db, invoice_id)
    return APIResponse(data=_build_invoice_response(invoice), message="Invoice sent")


@router.post("/invoices/{invoice_id}/cancel", response_model=APIResponse[InvoiceResponse])
@require_role(Role.SCHOOL_ADMIN)
async def cancel_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Cancel an invoice."""
    service = get_billing_service()
    invoice = await service.cancel_invoice(db, invoice_id)
    return APIResponse(data=_build_invoice_response(invoice), message="Invoice cancelled")


# =========================================================================
# Payments
# =========================================================================

@router.get("/payments", response_model=APIResponse[list[PaymentResponse]])
@require_role(Role.SCHOOL_ADMIN)
async def list_payments(
    student_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List payments."""
    service = get_billing_service()
    payments, total = await service.get_payments(
        db, student_id=student_id, page=page, page_size=page_size
    )
    total_pages = (total + page_size - 1) // page_size
    return APIResponse(
        data=[_build_payment_response(p) for p in payments],
        pagination=PaginationMeta(
            page=page, page_size=page_size, total_items=total,
            total_pages=total_pages, has_next=page < total_pages, has_prev=page > 1,
        ),
    )


@router.post("/payments", response_model=APIResponse[PaymentResponse])
@require_role(Role.SCHOOL_ADMIN)
async def record_payment(
    data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
):
    """Record a payment against an invoice."""
    service = get_billing_service()
    payment = await service.record_payment(db, data)
    return APIResponse(data=_build_payment_response(payment), message="Payment recorded")


@router.delete("/payments/{payment_id}", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN)
async def delete_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a payment and recalculate invoice balance."""
    service = get_billing_service()
    await service.delete_payment(db, payment_id)
    return APIResponse(message="Payment deleted")


# =========================================================================
# Statements & Balances
# =========================================================================

@router.get("/students/{student_id}/statement", response_model=APIResponse[StudentStatement])
@require_role(Role.SCHOOL_ADMIN, Role.PARENT)
async def get_student_statement(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get chronological statement for a student."""
    service = get_billing_service()
    statement = await service.get_student_statement(db, student_id)
    return APIResponse(data=statement)


@router.get("/students/{student_id}/balance", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN, Role.PARENT)
async def get_student_balance(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get current balance for a student."""
    service = get_billing_service()
    balance = await service.get_student_balance(db, student_id)
    return APIResponse(data=balance)


@router.get("/my-children/balances", response_model=APIResponse[list[ChildBalance]])
@require_role(Role.PARENT)
async def get_my_children_balances(
    db: AsyncSession = Depends(get_db),
):
    """Get balances for all of the parent's children."""
    service = get_billing_service()
    parent_id = get_current_user_id()
    balances = await service.get_children_balances(db, parent_id)
    return APIResponse(data=balances)


# =========================================================================
# Summary
# =========================================================================

@router.get("/summary", response_model=APIResponse[BillingSummary])
@require_role(Role.SCHOOL_ADMIN)
async def get_billing_summary(
    db: AsyncSession = Depends(get_db),
):
    """Get billing dashboard summary stats."""
    service = get_billing_service()
    summary = await service.get_billing_summary(db)
    return APIResponse(data=summary)
