"""Subscription and Paystack payment API routes."""

import hashlib
import hmac
import logging
import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.file_entity import FileCategory
from app.models.subscription import EftPaymentStatus, PlatformInvoice, SubscriptionStatus
from app.schemas.common import APIResponse, PaginationMeta
from app.services.file_service import get_file_service
from app.services.paystack_service import get_paystack_service
from app.services.subscription_service import get_subscription_service
from app.utils.permissions import require_role, require_super_admin
from app.utils.tenant_context import get_current_user_id, get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Subscriptions"])


# ── Schemas ────────────────────────────────────────────────


class PlanCreate(BaseModel):
    name: str
    description: str | None = None
    price_monthly: float
    price_annually: float | None = None
    max_students: int | None = None
    max_staff: int | None = None
    trial_days: int = 30
    features: dict | None = None


class PlanUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price_monthly: float | None = None
    price_annually: float | None = None
    max_students: int | None = None
    max_staff: int | None = None
    trial_days: int | None = None
    is_active: bool | None = None
    features: dict | None = None


class InitializePayment(BaseModel):
    """Request to initialize a Paystack checkout for subscription payment."""
    callback_url: str | None = None


class AssignPlan(BaseModel):
    """Super admin assigns a plan to a tenant."""
    tenant_id: str
    plan_id: str


class ExtendTrial(BaseModel):
    """Super admin extends a tenant's trial period."""
    days: int = Field(..., ge=1, le=365, description="Number of days to extend the trial")


# ── Super Admin: Plan Management ──────────────────────────


@router.get("/admin/subscription-plans")
@require_super_admin()
async def list_all_plans(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """List all subscription plans (super admin)."""
    service = get_subscription_service()
    plans = await service.list_plans(db, active_only=active_only)
    return APIResponse(
        status="success",
        data=[
            {
                "id": str(p.id),
                "name": p.name,
                "description": p.description,
                "price_monthly": float(p.price_monthly),
                "price_annually": float(p.price_annually) if p.price_annually else None,
                "currency": p.currency,
                "max_students": p.max_students,
                "max_staff": p.max_staff,
                "trial_days": p.trial_days,
                "is_active": p.is_active,
                "display_order": p.display_order,
                "paystack_plan_code": p.paystack_plan_code,
                "features": p.features,
            }
            for p in plans
        ],
    )


@router.post("/admin/subscription-plans")
@require_super_admin()
async def create_plan(
    body: PlanCreate,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Create a new subscription plan (super admin)."""
    from decimal import Decimal

    service = get_subscription_service()
    plan = await service.create_plan(
        db,
        name=body.name,
        description=body.description,
        price_monthly=Decimal(str(body.price_monthly)),
        price_annually=Decimal(str(body.price_annually)) if body.price_annually else None,
        max_students=body.max_students,
        max_staff=body.max_staff,
        trial_days=body.trial_days,
        features=body.features,
    )
    await db.commit()
    return APIResponse(
        status="success",
        data={"id": str(plan.id), "name": plan.name},
        message=f"Plan '{plan.name}' created",
    )


@router.put("/admin/subscription-plans/{plan_id}")
@require_super_admin()
async def update_plan(
    plan_id: uuid.UUID,
    body: PlanUpdate,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Update a subscription plan (super admin)."""
    from decimal import Decimal

    service = get_subscription_service()
    updates = body.model_dump(exclude_none=True)
    if "price_monthly" in updates:
        updates["price_monthly"] = Decimal(str(updates["price_monthly"]))
    if "price_annually" in updates:
        updates["price_annually"] = Decimal(str(updates["price_annually"]))

    plan = await service.update_plan(db, plan_id, **updates)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    await db.commit()
    return APIResponse(status="success", message="Plan updated")


@router.delete("/admin/subscription-plans/{plan_id}")
@require_super_admin()
async def delete_plan(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Soft-delete a subscription plan (super admin)."""
    service = get_subscription_service()
    deleted = await service.delete_plan(db, plan_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Plan not found")
    await db.commit()
    return APIResponse(status="success", message="Plan deleted")


# ── Super Admin: Subscription Management ──────────────────


@router.get("/admin/subscriptions")
@require_super_admin()
async def list_subscriptions(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """List all tenant subscriptions (super admin)."""
    from sqlalchemy import select, func
    from app.models.subscription import TenantSubscription

    stmt = select(TenantSubscription)
    count_stmt = select(func.count(TenantSubscription.id))

    if status:
        stmt = stmt.where(TenantSubscription.status == status.upper())
        count_stmt = count_stmt.where(TenantSubscription.status == status.upper())

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(TenantSubscription.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    subs = list(result.scalars().all())

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        status="success",
        data=[
            {
                "id": str(s.id),
                "tenant_id": str(s.tenant_id),
                "tenant_name": s.tenant.name if s.tenant else None,
                "plan_name": s.plan.name if s.plan else None,
                "plan_price": float(s.plan.price_monthly) if s.plan else None,
                "status": s.status,
                "trial_start": s.trial_start.isoformat() if s.trial_start else None,
                "trial_end": s.trial_end.isoformat() if s.trial_end else None,
                "current_period_end": s.current_period_end.isoformat() if s.current_period_end else None,
                "failed_payment_count": s.failed_payment_count,
                "created_at": s.created_at.isoformat(),
            }
            for s in subs
        ],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post("/admin/subscriptions/assign")
@require_super_admin()
async def assign_plan_to_tenant(
    body: AssignPlan,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Assign a subscription plan to a tenant (super admin)."""
    from app.models.tenant import Tenant

    service = get_subscription_service()
    tenant = await db.get(Tenant, uuid.UUID(body.tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    sub = await service.initialize_tenant_subscription(
        db,
        tenant_id=tenant.id,
        tenant_email=tenant.email,
        tenant_name=tenant.name,
        plan_id=uuid.UUID(body.plan_id),
    )
    await db.commit()
    return APIResponse(
        status="success",
        message=f"Trial started for {tenant.name}",
        data={"subscription_id": str(sub.id), "trial_end": sub.trial_end.isoformat()},
    )


@router.post("/admin/subscriptions/{subscription_id}/extend-trial")
@require_super_admin()
async def extend_trial(
    subscription_id: uuid.UUID,
    body: ExtendTrial,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Extend a tenant's trial period by a custom number of days (super admin)."""
    service = get_subscription_service()
    try:
        sub = await service.extend_trial(db, subscription_id, body.days)
        await db.commit()
        return APIResponse(
            status="success",
            message=f"Trial extended by {body.days} days. New end date: {sub.trial_end.isoformat()}",
            data={
                "subscription_id": str(sub.id),
                "trial_end": sub.trial_end.isoformat(),
                "status": sub.status,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/subscription-revenue")
@require_super_admin()
async def get_revenue(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Get platform revenue statistics (super admin)."""
    service = get_subscription_service()
    stats = await service.get_revenue_stats(db)
    return APIResponse(status="success", data=stats)


@router.get("/admin/platform-invoices")
@require_super_admin()
async def list_platform_invoices(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """List all platform invoices (super admin)."""
    service = get_subscription_service()
    invoices, total = await service.get_all_invoices(db, page, page_size, status)
    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        status="success",
        data=[
            {
                "id": str(inv.id),
                "tenant_name": inv.tenant.name if inv.tenant else None,
                "amount": float(inv.amount),
                "currency": inv.currency,
                "status": inv.status,
                "billing_period_start": inv.billing_period_start.isoformat(),
                "billing_period_end": inv.billing_period_end.isoformat(),
                "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
                "paystack_reference": inv.paystack_reference,
                "created_at": inv.created_at.isoformat(),
            }
            for inv in invoices
        ],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


# ── Tenant-facing: My subscription ────────────────────────


@router.get("/subscription")
@require_role("SCHOOL_ADMIN")
async def get_my_subscription(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Get the current tenant's subscription status.

    Includes the tenant's effective ``currency_code`` + ``currency_symbol``
    so the /subscription page can render prices in the right currency
    for the tenant's jurisdiction — a Zim tenant sees "$", a SA tenant
    sees "R", etc. Falls back to R (ZAR) when nothing is configured.
    """
    from app.models.tenant import Tenant
    from app.services import jurisdiction_service

    tenant_id = get_tenant_id()
    service = get_subscription_service()
    sub = await service.get_tenant_subscription(db, tenant_id)

    if not sub:
        return APIResponse(status="success", data=None, message="No subscription found")

    tenant = await db.get(Tenant, tenant_id)
    jurisdiction = await jurisdiction_service.get_jurisdiction_for_tenant(db, tenant)

    return APIResponse(
        status="success",
        data={
            "id": str(sub.id),
            "plan_name": sub.plan.name if sub.plan else None,
            "plan_price": float(sub.plan.price_monthly) if sub.plan else None,
            "status": sub.status,
            "trial_start": sub.trial_start.isoformat() if sub.trial_start else None,
            "trial_end": sub.trial_end.isoformat() if sub.trial_end else None,
            "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
            "is_active": await service.is_subscription_active(db, tenant_id),
            "max_students": sub.plan.max_students if sub.plan else None,
            "max_staff": sub.plan.max_staff if sub.plan else None,
            # Jurisdiction-aware money display. The page uses these
            # instead of hardcoding "R".
            "currency_code": jurisdiction.currency_code,
            "currency_symbol": jurisdiction.currency_symbol,
            "country_code": jurisdiction.country_code,
        },
    )


class SelectPlan(BaseModel):
    plan_id: str


@router.post("/subscription/select-plan")
@require_role("SCHOOL_ADMIN")
async def select_plan(
    body: SelectPlan,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Select or switch to a different subscription plan.

    If currently on a trial, switches the plan (resets trial period).
    If active/paid, schedules the change for next billing cycle.
    """
    tenant_id = get_tenant_id()
    service = get_subscription_service()
    plan = await service.get_plan(db, uuid.UUID(body.plan_id))
    if not plan or not plan.is_active:
        raise HTTPException(status_code=404, detail="Plan not found")

    sub = await service.get_tenant_subscription(db, tenant_id)
    if sub and sub.plan_id == plan.id:
        return APIResponse(status="success", message="Already on this plan")

    if sub and sub.status in (
        SubscriptionStatus.TRIALING.value,
        SubscriptionStatus.SUSPENDED.value,
        SubscriptionStatus.CANCELLED.value,
    ):
        # On trial / suspended / cancelled → switch plan and restart trial
        sub.plan_id = plan.id
        from datetime import date, timedelta
        today = date.today()
        sub.trial_start = today
        sub.trial_end = today + timedelta(days=plan.trial_days)
        sub.current_period_start = today
        sub.current_period_end = sub.trial_end
        sub.status = SubscriptionStatus.TRIALING.value
        await db.flush()
        await service.sync_tenant_features(db, tenant_id, plan)
    elif sub:
        # Active / past due → just switch the plan, keep billing cycle
        sub.plan_id = plan.id
        await db.flush()
        await service.sync_tenant_features(db, tenant_id, plan)
    else:
        # No subscription at all → start fresh trial
        sub = await service.start_trial(db, tenant_id, plan.id)

    await db.commit()
    return APIResponse(
        status="success",
        message=f"Switched to {plan.name} plan",
        data={
            "plan_name": plan.name,
            "trial_end": sub.trial_end.isoformat() if sub.trial_end else None,
        },
    )


@router.get("/subscription/invoices")
@require_role("SCHOOL_ADMIN")
async def get_my_invoices(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Get the current tenant's platform invoices."""
    tenant_id = get_tenant_id()
    service = get_subscription_service()
    invoices = await service.get_tenant_invoices(db, tenant_id)

    return APIResponse(
        status="success",
        data=[
            {
                "id": str(inv.id),
                "amount": float(inv.amount),
                "currency": inv.currency,
                "status": inv.status,
                "billing_period_start": inv.billing_period_start.isoformat(),
                "billing_period_end": inv.billing_period_end.isoformat(),
                "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
            }
            for inv in invoices
        ],
    )


@router.post("/subscription/initialize-payment")
@require_role("SCHOOL_ADMIN")
async def initialize_payment(
    body: InitializePayment,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Start a hosted-checkout for the school's next subscription bill.

    Uses the platform's configured payment gateway (Yoco, Paynow, …).
    If a PENDING PlatformInvoice for this tenant already exists it's
    reused — no duplicate invoices. Otherwise a fresh one is minted for
    the next billing period so trialing tenants who click "Pay now"
    early get an invoice created on demand.

    Returns ``redirect_url`` — the frontend redirects the browser there.
    EFT-with-POP remains a separate flow (POST /subscription/eft-payments).
    """
    from datetime import date, timedelta
    from app.config import get_settings as get_app_settings
    from app.models.subscription import PlatformInvoiceStatus
    from app.models.tenant import Tenant
    from app.services import gateway_service
    from sqlalchemy import select

    tenant_id = get_tenant_id()
    service = get_subscription_service()
    sub = await service.get_tenant_subscription(db, tenant_id)
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")
    if not sub.plan:
        raise HTTPException(status_code=400, detail="Pick a plan first")

    # Route through the configured gateway — Paystack no longer hard-coded.
    provider = await gateway_service.get_active_provider(db)
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Card payments aren't enabled on this platform yet. "
                "The super admin can configure a gateway at "
                "/admin/payment-gateways, or you can pay by EFT below."
            ),
        )

    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Find-or-create a PENDING invoice for this tenant. Reusing a pending
    # invoice keeps the tenant from stacking up duplicates when they
    # bounce off the checkout page and click Pay again.
    q = select(PlatformInvoice).where(
        PlatformInvoice.tenant_id == tenant_id,
        PlatformInvoice.status == PlatformInvoiceStatus.PENDING.value,
    ).order_by(PlatformInvoice.created_at.desc())
    invoice = (await db.execute(q)).scalars().first()
    if invoice is None:
        # Next period starts today (or when trial ends, whichever is
        # sooner). Keeping the period 30 days matches handle_payment_success.
        today = date.today()
        start = min(sub.trial_end or today, today) if sub.trial_end else today
        # If tenant is mid-cycle, extend from current_period_end instead.
        if sub.current_period_end and sub.current_period_end > today:
            start = sub.current_period_end
        invoice = PlatformInvoice(
            tenant_id=tenant_id,
            subscription_id=sub.id,
            amount=sub.plan.price_monthly,
            currency=(sub.plan.currency if getattr(sub.plan, "currency", None) else "ZAR"),
            status=PlatformInvoiceStatus.PENDING.value,
            billing_period_start=start,
            billing_period_end=start + timedelta(days=30),
        )
        db.add(invoice)
        await db.flush()  # get invoice.id before create_checkout

    # Redirect back to the /subscription page after payment; the query
    # string lets the page verify + refresh.
    app_settings = get_app_settings()
    base = app_settings.app_base_url.rstrip("/")
    callback = body.callback_url or f"{base}/subscription?paid=1"
    cancel_url = f"{base}/subscription?cancelled=1"

    try:
        result = await provider.create_checkout(
            invoice, return_url=callback, cancel_url=cancel_url,
        )
    except Exception as e:
        logger.exception("Gateway checkout creation failed")
        # 502 is more accurate than 500 — the failure originates upstream.
        raise HTTPException(status_code=502, detail=str(e))

    # Persist the provider ref so a webhook that comes back with just
    # the reference can be reconciled to this invoice.
    invoice.paystack_reference = result.reference
    await db.commit()

    return APIResponse(
        status="success",
        message="Redirecting to payment page…",
        data={
            "redirect_url": result.redirect_url,
            # Legacy alias — old clients read authorization_url. Safe to
            # remove once the subscription page has been redeployed.
            "authorization_url": result.redirect_url,
            "reference": result.reference,
            "provider_id": provider.provider_id,
            "invoice_id": str(invoice.id),
        },
    )


@router.get("/subscription/verify-payment")
@require_role("SCHOOL_ADMIN")
async def verify_payment(
    reference: str,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Verify a Paystack payment after redirect back from checkout."""
    paystack = get_paystack_service()
    if not paystack.is_configured:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")

    try:
        txn = await paystack.verify_transaction(reference)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Verification failed: {str(e)}")

    if txn.get("status") != "success":
        return APIResponse(
            status="error",
            message=f"Payment not successful: {txn.get('gateway_response', 'Unknown error')}",
        )

    tenant_id = get_tenant_id()
    service = get_subscription_service()

    authorization = txn.get("authorization", {})
    customer = txn.get("customer", {})

    await service.handle_payment_success(
        db,
        tenant_id=tenant_id,
        paystack_reference=reference,
        paystack_transaction_id=str(txn.get("id", "")),
        amount_cents=txn.get("amount", 0),
        authorization_code=authorization.get("authorization_code"),
        customer_code=customer.get("customer_code"),
    )
    await db.commit()

    return APIResponse(status="success", message="Payment verified and subscription activated")


@router.post("/subscription/cancel")
@require_role("SCHOOL_ADMIN")
async def cancel_my_subscription(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Cancel the current tenant's subscription."""
    tenant_id = get_tenant_id()
    service = get_subscription_service()
    sub = await service.cancel_subscription(db, tenant_id)
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")
    await db.commit()
    return APIResponse(status="success", message="Subscription cancelled")


# ── Public: Available plans ────────────────────────────────


@router.get("/plans")
async def list_public_plans(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """List available subscription plans (public, no auth required)."""
    service = get_subscription_service()
    plans = await service.list_plans(db, active_only=True)
    return APIResponse(
        status="success",
        data=[
            {
                "id": str(p.id),
                "name": p.name,
                "description": p.description,
                "price_monthly": float(p.price_monthly),
                "price_annually": float(p.price_annually) if p.price_annually else None,
                "currency": p.currency,
                "max_students": p.max_students,
                "max_staff": p.max_staff,
                "trial_days": p.trial_days,
                "features": p.features,
            }
            for p in plans
        ],
    )


# ── Paystack Webhook ──────────────────────────────────────


@router.post("/paystack/webhook")
async def paystack_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle Paystack webhook events.

    Paystack sends events for:
    - charge.success — payment succeeded
    - charge.failed — payment failed
    - subscription.create — subscription created
    - subscription.not_renew — subscription won't renew
    - subscription.disable — subscription disabled
    - invoice.create — upcoming charge
    - invoice.payment_failed — recurring charge failed
    """
    body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    # Verify webhook signature
    paystack = get_paystack_service()
    if paystack.is_configured and not paystack.verify_webhook_signature(body, signature):
        logger.warning("Invalid Paystack webhook signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    import json
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event", "")
    data = payload.get("data", {})

    logger.info(f"Paystack webhook: {event}")

    service = get_subscription_service()

    if event == "charge.success":
        metadata = data.get("metadata", {})
        tenant_id_str = metadata.get("tenant_id")
        if tenant_id_str:
            tenant_id = uuid.UUID(tenant_id_str)
            authorization = data.get("authorization", {})
            customer = data.get("customer", {})
            await service.handle_payment_success(
                db,
                tenant_id=tenant_id,
                paystack_reference=data.get("reference", ""),
                paystack_transaction_id=str(data.get("id", "")),
                amount_cents=data.get("amount", 0),
                authorization_code=authorization.get("authorization_code"),
                customer_code=customer.get("customer_code"),
            )
            await db.commit()

    elif event in ("charge.failed", "invoice.payment_failed"):
        metadata = data.get("metadata", {})
        tenant_id_str = metadata.get("tenant_id")
        if tenant_id_str:
            tenant_id = uuid.UUID(tenant_id_str)
            await service.handle_payment_failure(
                db,
                tenant_id=tenant_id,
                paystack_reference=data.get("reference"),
                failure_reason=data.get("gateway_response"),
            )
            await db.commit()

    elif event == "subscription.disable":
        # Subscription was disabled on Paystack side
        customer = data.get("customer", {})
        customer_code = customer.get("customer_code")
        if customer_code:
            from sqlalchemy import select
            from app.models.subscription import TenantSubscription
            stmt = select(TenantSubscription).where(
                TenantSubscription.paystack_customer_code == customer_code
            )
            result = await db.execute(stmt)
            sub = result.scalar_one_or_none()
            if sub:
                sub.status = SubscriptionStatus.CANCELLED.value
                await db.commit()

    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────
# Manual EFT payment flow
# ─────────────────────────────────────────────────────────────────


class PlatformBankingUpdate(BaseModel):
    bank_name: str | None = None
    account_holder: str | None = None
    account_number: str | None = None
    branch_code: str | None = None
    account_type: str | None = None
    swift_code: str | None = None
    reference_instructions: str | None = None
    notify_email: str | None = None


class ApproveEftPayment(BaseModel):
    extend_period_days: int | None = Field(
        None, ge=1, le=3650,
        description="Days to extend the subscription (defaults to 30)"
    )


class RejectEftPayment(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


def _eft_payment_to_dict(p) -> dict:
    """Serialise a PlatformEftPayment for API responses."""
    return {
        "id": str(p.id),
        "tenant_id": str(p.tenant_id),
        "tenant_name": p.tenant.name if p.tenant else None,
        "subscription_id": str(p.subscription_id) if p.subscription_id else None,
        "platform_invoice_id": (
            str(p.platform_invoice_id) if p.platform_invoice_id else None
        ),
        "amount": float(p.amount),
        "currency": p.currency,
        "reference": p.reference,
        "notes": p.notes,
        "status": p.status,
        "pop_file_id": str(p.pop_file_id) if p.pop_file_id else None,
        "pop_file_name": p.pop_file.original_name if p.pop_file else None,
        "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
        "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
        "reviewed_by": str(p.reviewed_by) if p.reviewed_by else None,
        "reviewer_name": (
            f"{p.reviewer.first_name} {p.reviewer.last_name}"
            if p.reviewer
            else None
        ),
        "rejection_reason": p.rejection_reason,
        "extend_period_days": p.extend_period_days,
    }


# ── Super admin: banking details ─────────────────────────────


@router.get("/admin/platform-banking")
@require_super_admin()
async def get_platform_banking(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Return the platform's banking details (super admin)."""
    service = get_subscription_service()
    banking = await service.get_platform_banking(db)
    return APIResponse(status="success", data=banking)


@router.put("/admin/platform-banking")
@require_super_admin()
async def update_platform_banking(
    body: PlatformBankingUpdate,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Update the platform's banking details (super admin)."""
    service = get_subscription_service()
    clean = {k: v for k, v in body.model_dump().items() if v is not None}
    saved = await service.update_platform_banking(db, clean)
    await db.commit()
    return APIResponse(
        status="success",
        message="Banking details updated",
        data=saved,
    )


# ── Tenant: view banking + submit EFT ────────────────────────


@router.get("/subscription/banking")
@require_role("SCHOOL_ADMIN")
async def get_banking_for_tenant(
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Tenant fetches the platform's banking details to pay into."""
    service = get_subscription_service()
    banking = await service.get_platform_banking(db)
    if not banking:
        return APIResponse(
            status="success",
            data=None,
            message="The platform's banking details haven't been configured yet. Contact support.",
        )
    return APIResponse(status="success", data=banking)


@router.post("/subscription/eft-payment")
@require_role("SCHOOL_ADMIN")
async def submit_eft_payment(
    amount: str = Form(..., description="Amount paid in ZAR"),
    reference: str = Form(..., description="Payment reference used on the transfer"),
    pop: UploadFile = File(..., description="Proof of payment (PDF or image)"),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """School admin submits an EFT payment with proof (multipart form)."""
    # Parse amount
    try:
        amount_decimal = Decimal(amount.strip())
    except (InvalidOperation, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid amount")

    if amount_decimal <= 0:
        raise HTTPException(status_code=400, detail="Amount must be > 0")

    tenant_id = get_tenant_id()

    # Upload PoP file
    file_service = get_file_service()
    try:
        file_entity = await file_service.upload_file(
            db, pop, FileCategory.DOCUMENT, entity_id=tenant_id
        )
    except Exception as e:
        logger.exception("Failed to upload PoP file")
        raise HTTPException(status_code=400, detail=f"Could not upload PoP: {e}")

    await db.flush()
    await db.refresh(file_entity)

    service = get_subscription_service()
    try:
        payment = await service.submit_eft_payment(
            db,
            tenant_id=tenant_id,
            amount=amount_decimal,
            reference=reference,
            pop_file_id=file_entity.id,
            notes=notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()

    # Notify super admin(s) via notification service
    try:
        from app.services.notification_service import get_notification_service
        from app.models.user import Role, User
        from sqlalchemy import select
        q = await db.execute(
            select(User.id).where(
                User.role == Role.SUPER_ADMIN.value,
                User.is_active == True,  # noqa: E712
                User.deleted_at.is_(None),
            )
        )
        admin_ids = [row[0] for row in q.all()]
        if admin_ids:
            notif_service = get_notification_service()
            await notif_service.create_bulk_notifications(
                db=db,
                user_ids=admin_ids,
                title="EFT payment awaiting approval",
                body=(
                    f"A tenant submitted an EFT payment of {payment.currency} "
                    f"{payment.amount:.2f} (ref: {payment.reference})."
                ),
                notification_type="PAYMENT_RECEIVED",
                reference_type="eft_payment",
                reference_id=payment.id,
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to send super-admin notification for EFT submission")

    return APIResponse(
        status="success",
        message="EFT payment submitted. We'll review and confirm within 1 business day.",
        data=_eft_payment_to_dict(payment),
    )


@router.get("/subscription/eft-payments")
@require_role("SCHOOL_ADMIN")
async def list_my_eft_payments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Tenant lists their own EFT payment history."""
    tenant_id = get_tenant_id()
    service = get_subscription_service()
    items, total = await service.list_eft_payments(
        db, tenant_id=tenant_id, page=page, page_size=page_size
    )
    total_pages = (total + page_size - 1) // page_size
    return APIResponse(
        status="success",
        data=[_eft_payment_to_dict(p) for p in items],
        pagination=PaginationMeta(
            page=page, page_size=page_size, total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages, has_prev=page > 1,
        ),
    )


# ── Super admin: EFT queue ───────────────────────────────────


@router.get("/admin/eft-payments")
@require_super_admin()
async def list_eft_payments_admin(
    status: str | None = Query(None, description="Filter by status (PENDING/APPROVED/REJECTED)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Super admin lists all EFT payment submissions."""
    service = get_subscription_service()
    items, total = await service.list_eft_payments(
        db, status=status, page=page, page_size=page_size
    )
    total_pages = (total + page_size - 1) // page_size
    return APIResponse(
        status="success",
        data=[_eft_payment_to_dict(p) for p in items],
        pagination=PaginationMeta(
            page=page, page_size=page_size, total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages, has_prev=page > 1,
        ),
    )


@router.post("/admin/eft-payments/{payment_id}/approve")
@require_super_admin()
async def approve_eft_payment(
    payment_id: uuid.UUID,
    body: ApproveEftPayment,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Super admin approves an EFT payment and activates the subscription."""
    reviewer_id = get_current_user_id()
    service = get_subscription_service()
    try:
        payment = await service.approve_eft_payment(
            db,
            payment_id=payment_id,
            reviewer_id=reviewer_id,
            extend_period_days=body.extend_period_days,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()

    # Notify tenant admins of approval
    try:
        from app.services.notification_service import get_notification_service
        from app.models.user import Role, User
        from sqlalchemy import select
        q = await db.execute(
            select(User.id).where(
                User.tenant_id == payment.tenant_id,
                User.role == Role.SCHOOL_ADMIN.value,
                User.is_active == True,  # noqa: E712
                User.deleted_at.is_(None),
            )
        )
        admin_ids = [row[0] for row in q.all()]
        if admin_ids:
            notif_service = get_notification_service()
            await notif_service.create_bulk_notifications(
                db=db,
                user_ids=admin_ids,
                title="Payment approved — subscription active",
                body=(
                    f"Your EFT payment of {payment.currency} {payment.amount:.2f} "
                    f"has been approved. Your subscription is now active."
                ),
                notification_type="PAYMENT_RECEIVED",
                reference_type="eft_payment",
                reference_id=payment.id,
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to notify tenant admins on EFT approval")

    return APIResponse(
        status="success",
        message="Payment approved and subscription activated",
        data=_eft_payment_to_dict(payment),
    )


@router.post("/admin/eft-payments/{payment_id}/reject")
@require_super_admin()
async def reject_eft_payment(
    payment_id: uuid.UUID,
    body: RejectEftPayment,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Super admin rejects an EFT payment with a reason."""
    reviewer_id = get_current_user_id()
    service = get_subscription_service()
    try:
        payment = await service.reject_eft_payment(
            db,
            payment_id=payment_id,
            reviewer_id=reviewer_id,
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()

    # Notify tenant
    try:
        from app.services.notification_service import get_notification_service
        from app.models.user import Role, User
        from sqlalchemy import select
        q = await db.execute(
            select(User.id).where(
                User.tenant_id == payment.tenant_id,
                User.role == Role.SCHOOL_ADMIN.value,
                User.is_active == True,  # noqa: E712
                User.deleted_at.is_(None),
            )
        )
        admin_ids = [row[0] for row in q.all()]
        if admin_ids:
            notif_service = get_notification_service()
            await notif_service.create_bulk_notifications(
                db=db,
                user_ids=admin_ids,
                title="EFT payment was not approved",
                body=(
                    f"Your EFT payment of {payment.currency} {payment.amount:.2f} "
                    f"(ref: {payment.reference}) was not approved. "
                    f"Reason: {payment.rejection_reason}"
                ),
                notification_type="PAYMENT_RECEIVED",
                reference_type="eft_payment",
                reference_id=payment.id,
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to notify tenant admins on EFT rejection")

    return APIResponse(
        status="success",
        message="Payment rejected; tenant has been notified",
        data=_eft_payment_to_dict(payment),
    )


# ============================================================================
# Gateway-based payment for a specific platform invoice
# ============================================================================

@router.get("/subscription/gateway")
@require_role("SCHOOL_ADMIN")
async def get_active_gateway_for_subscription(
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Tell the /subscription page whether card payments are available on
    this instance so it can show or hide the Pay-by-card option."""
    from app.services import gateway_service

    cfg = await gateway_service.get_config(db)
    if not cfg.configured:
        return APIResponse(
            status="success",
            data={"configured": False, "provider_id": "", "display_name": ""},
        )
    cls = gateway_service.PROVIDER_REGISTRY.get(cfg.provider_id)
    return APIResponse(
        status="success",
        data={
            "configured": True,
            "provider_id": cfg.provider_id,
            "display_name": cls.display_name if cls else cfg.provider_id,
        },
    )




@router.post("/subscription/invoices/{invoice_id}/pay")
@require_role("SCHOOL_ADMIN")
async def pay_platform_invoice_via_gateway(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Create a hosted-checkout session for the active payment gateway and
    return the redirect URL. The school admin's browser then loads that URL
    to complete the payment on the gateway's hosted page.

    EFT-with-POP flow remains separate (POST /subscription/eft-payments).
    """
    from app.config import get_settings as get_app_settings
    from app.models.subscription import PlatformInvoiceStatus
    from app.services import gateway_service

    tenant_id = get_tenant_id()

    # Load the invoice and confirm it belongs to this tenant
    invoice = await db.get(PlatformInvoice, invoice_id)
    if not invoice or invoice.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == PlatformInvoiceStatus.PAID.value:
        raise HTTPException(status_code=400, detail="Invoice is already paid")

    provider = await gateway_service.get_active_provider(db)
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail="Card payments aren't enabled on this platform. Use EFT instead.",
        )

    app_settings = get_app_settings()
    base = app_settings.app_base_url.rstrip("/")
    return_url = f"{base}/subscription?paid=1"
    cancel_url = f"{base}/subscription?cancelled=1"

    try:
        result = await provider.create_checkout(
            invoice, return_url=return_url, cancel_url=cancel_url
        )
    except Exception as e:
        logger.exception("Gateway checkout creation failed")
        raise HTTPException(status_code=502, detail=str(e))

    # Persist the provider reference so we can reconcile later if needed
    invoice.paystack_reference = result.reference
    await db.commit()

    return APIResponse(
        status="success",
        message="Redirecting to payment page…",
        data={
            "redirect_url": result.redirect_url,
            "reference": result.reference,
            "provider_id": provider.provider_id,
        },
    )
