"""Subscription management service for tenant billing."""

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.subscription import (
    PlatformInvoice,
    PlatformInvoiceStatus,
    SubscriptionPlan,
    SubscriptionStatus,
    TenantSubscription,
)
from app.models.tenant import Tenant
from app.services.paystack_service import get_paystack_service

logger = logging.getLogger(__name__)

GRACE_PERIOD_DAYS = 7
MAX_FAILED_PAYMENTS = 3


class SubscriptionService:
    """Manages subscription plans, tenant subscriptions, and billing."""

    # ── Plans ──────────────────────────────────────────────────

    async def list_plans(self, db: AsyncSession, active_only: bool = True) -> list[SubscriptionPlan]:
        stmt = select(SubscriptionPlan)
        if active_only:
            stmt = stmt.where(
                SubscriptionPlan.is_active == True,
                SubscriptionPlan.deleted_at == None,
            )
        else:
            stmt = stmt.where(SubscriptionPlan.deleted_at == None)
        stmt = stmt.order_by(SubscriptionPlan.display_order)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_plan(self, db: AsyncSession, plan_id: uuid.UUID) -> SubscriptionPlan | None:
        return await db.get(SubscriptionPlan, plan_id)

    async def create_plan(
        self, db: AsyncSession, name: str, price_monthly: Decimal,
        description: str | None = None, max_students: int | None = None,
        max_staff: int | None = None, trial_days: int = 30,
        price_annually: Decimal | None = None, features: dict | None = None,
    ) -> SubscriptionPlan:
        plan = SubscriptionPlan(
            name=name,
            description=description,
            price_monthly=price_monthly,
            price_annually=price_annually,
            max_students=max_students,
            max_staff=max_staff,
            trial_days=trial_days,
            features=features,
        )

        # Create plan on Paystack if configured
        paystack = get_paystack_service()
        if paystack.is_configured:
            try:
                ps_plan = await paystack.create_plan(
                    name=name,
                    amount_cents=paystack.rands_to_cents(price_monthly),
                    interval="monthly",
                    description=description,
                )
                plan.paystack_plan_code = ps_plan.get("plan_code")
            except Exception:
                logger.exception("Failed to create Paystack plan")

        db.add(plan)
        await db.flush()
        return plan

    async def update_plan(
        self, db: AsyncSession, plan_id: uuid.UUID, **kwargs
    ) -> SubscriptionPlan | None:
        plan = await db.get(SubscriptionPlan, plan_id)
        if not plan:
            return None
        for key, value in kwargs.items():
            if hasattr(plan, key):
                setattr(plan, key, value)
        await db.flush()
        return plan

    async def delete_plan(self, db: AsyncSession, plan_id: uuid.UUID) -> bool:
        plan = await db.get(SubscriptionPlan, plan_id)
        if not plan:
            return False
        plan.deleted_at = datetime.now(timezone.utc)
        plan.is_active = False
        await db.flush()
        return True

    # ── Subscriptions ──────────────────────────────────────────

    async def get_tenant_subscription(
        self, db: AsyncSession, tenant_id: uuid.UUID
    ) -> TenantSubscription | None:
        """Get the current (most recent) subscription for a tenant."""
        stmt = (
            select(TenantSubscription)
            .where(TenantSubscription.tenant_id == tenant_id)
            .order_by(TenantSubscription.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def start_trial(
        self, db: AsyncSession, tenant_id: uuid.UUID, plan_id: uuid.UUID
    ) -> TenantSubscription:
        """Start a free trial for a tenant."""
        plan = await db.get(SubscriptionPlan, plan_id)
        if not plan:
            raise ValueError("Plan not found")

        today = date.today()
        trial_end = today + timedelta(days=plan.trial_days)

        subscription = TenantSubscription(
            tenant_id=tenant_id,
            plan_id=plan_id,
            status=SubscriptionStatus.TRIALING.value,
            trial_start=today,
            trial_end=trial_end,
            current_period_start=today,
            current_period_end=trial_end,
        )
        db.add(subscription)
        await db.flush()

        # Sync plan features to tenant settings
        await self.sync_tenant_features(db, tenant_id, plan)

        logger.info(
            f"Started trial for tenant {tenant_id} on plan {plan.name}, "
            f"expires {trial_end}"
        )
        return subscription

    async def extend_trial(
        self, db: AsyncSession, subscription_id: uuid.UUID, days: int
    ) -> TenantSubscription:
        """Extend a tenant's trial period by a given number of days (super admin)."""
        sub = await db.get(TenantSubscription, subscription_id)
        if not sub:
            raise ValueError("Subscription not found")

        if days < 1 or days > 365:
            raise ValueError("Extension must be between 1 and 365 days")

        # If suspended/cancelled trial, reactivate it
        if sub.status in (SubscriptionStatus.SUSPENDED.value, SubscriptionStatus.CANCELLED.value):
            sub.status = SubscriptionStatus.TRIALING.value

        new_trial_end = (sub.trial_end or date.today()) + timedelta(days=days)
        sub.trial_end = new_trial_end
        sub.current_period_end = new_trial_end
        await db.flush()

        logger.info(
            f"Extended trial for subscription {subscription_id} by {days} days, "
            f"new end date: {new_trial_end}"
        )
        return sub

    async def activate_subscription(
        self, db: AsyncSession, tenant_id: uuid.UUID,
        paystack_authorization_code: str | None = None,
        paystack_customer_code: str | None = None,
    ) -> TenantSubscription | None:
        """Activate a subscription after successful payment/card capture."""
        sub = await self.get_tenant_subscription(db, tenant_id)
        if not sub:
            return None

        today = date.today()
        sub.status = SubscriptionStatus.ACTIVE.value
        sub.current_period_start = today
        sub.current_period_end = today + timedelta(days=30)
        sub.failed_payment_count = 0
        sub.grace_period_end = None

        if paystack_authorization_code:
            sub.paystack_authorization_code = paystack_authorization_code
        if paystack_customer_code:
            sub.paystack_customer_code = paystack_customer_code

        await db.flush()

        # Sync plan features to tenant settings
        if sub.plan:
            await self.sync_tenant_features(db, tenant_id, sub.plan)

        return sub

    async def handle_payment_success(
        self, db: AsyncSession, tenant_id: uuid.UUID,
        paystack_reference: str, paystack_transaction_id: str,
        amount_cents: int, authorization_code: str | None = None,
        customer_code: str | None = None,
    ) -> None:
        """Handle a successful payment from Paystack webhook."""
        sub = await self.get_tenant_subscription(db, tenant_id)
        if not sub:
            logger.warning(f"No subscription found for tenant {tenant_id}")
            return

        paystack = get_paystack_service()
        amount = paystack.cents_to_rands(amount_cents)

        # Record the platform invoice
        today = date.today()
        invoice = PlatformInvoice(
            tenant_id=tenant_id,
            subscription_id=sub.id,
            amount=amount,
            status=PlatformInvoiceStatus.PAID.value,
            billing_period_start=sub.current_period_end or today,
            billing_period_end=(sub.current_period_end or today) + timedelta(days=30),
            paystack_reference=paystack_reference,
            paystack_transaction_id=paystack_transaction_id,
            paid_at=datetime.now(timezone.utc),
        )
        db.add(invoice)

        # Extend the subscription period
        sub.status = SubscriptionStatus.ACTIVE.value
        sub.current_period_start = invoice.billing_period_start
        sub.current_period_end = invoice.billing_period_end
        sub.failed_payment_count = 0
        sub.grace_period_end = None

        if authorization_code:
            sub.paystack_authorization_code = authorization_code
        if customer_code:
            sub.paystack_customer_code = customer_code

        await db.flush()

        # Sync plan features to tenant settings
        if sub.plan:
            await self.sync_tenant_features(db, tenant_id, sub.plan)

        logger.info(f"Payment recorded for tenant {tenant_id}: R{amount}")

    async def handle_payment_failure(
        self, db: AsyncSession, tenant_id: uuid.UUID,
        paystack_reference: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        """Handle a failed payment from Paystack webhook."""
        sub = await self.get_tenant_subscription(db, tenant_id)
        if not sub:
            return

        sub.failed_payment_count += 1
        today = date.today()

        if sub.failed_payment_count >= MAX_FAILED_PAYMENTS:
            sub.status = SubscriptionStatus.SUSPENDED.value
            logger.warning(
                f"Tenant {tenant_id} suspended after {MAX_FAILED_PAYMENTS} failed payments"
            )
        elif sub.status != SubscriptionStatus.PAST_DUE.value:
            sub.status = SubscriptionStatus.PAST_DUE.value
            sub.grace_period_end = today + timedelta(days=GRACE_PERIOD_DAYS)

        # Record failed invoice
        if paystack_reference:
            invoice = PlatformInvoice(
                tenant_id=tenant_id,
                subscription_id=sub.id,
                amount=sub.plan.price_monthly if sub.plan else Decimal("0"),
                status=PlatformInvoiceStatus.FAILED.value,
                billing_period_start=sub.current_period_end or today,
                billing_period_end=(sub.current_period_end or today) + timedelta(days=30),
                paystack_reference=paystack_reference,
                failure_reason=failure_reason,
            )
            db.add(invoice)

        await db.flush()

    async def cancel_subscription(
        self, db: AsyncSession, tenant_id: uuid.UUID
    ) -> TenantSubscription | None:
        """Cancel a tenant's subscription."""
        sub = await self.get_tenant_subscription(db, tenant_id)
        if not sub:
            return None

        sub.status = SubscriptionStatus.CANCELLED.value
        sub.cancelled_at = datetime.now(timezone.utc)

        # Disable on Paystack
        paystack = get_paystack_service()
        if (
            paystack.is_configured
            and sub.paystack_subscription_code
            and sub.paystack_email_token
        ):
            try:
                await paystack.disable_subscription(
                    sub.paystack_subscription_code,
                    sub.paystack_email_token,
                )
            except Exception:
                logger.exception("Failed to disable Paystack subscription")

        await db.flush()
        return sub

    # ── Status checks ──────────────────────────────────────────

    async def is_subscription_active(
        self, db: AsyncSession, tenant_id: uuid.UUID
    ) -> bool:
        """Check if a tenant has an active or trialing subscription."""
        sub = await self.get_tenant_subscription(db, tenant_id)
        if not sub:
            return False

        today = date.today()

        if sub.status == SubscriptionStatus.TRIALING.value:
            return sub.trial_end is not None and sub.trial_end >= today

        if sub.status == SubscriptionStatus.ACTIVE.value:
            return True

        if sub.status == SubscriptionStatus.PAST_DUE.value:
            return sub.grace_period_end is not None and sub.grace_period_end >= today

        return False

    async def check_expired_trials(self, db: AsyncSession) -> list[uuid.UUID]:
        """Find and suspend tenants with expired trials (no payment method)."""
        today = date.today()
        stmt = select(TenantSubscription).where(
            TenantSubscription.status == SubscriptionStatus.TRIALING.value,
            TenantSubscription.trial_end < today,
            TenantSubscription.paystack_authorization_code == None,
        )
        result = await db.execute(stmt)
        expired = list(result.scalars().all())

        suspended_tenant_ids = []
        for sub in expired:
            sub.status = SubscriptionStatus.SUSPENDED.value
            suspended_tenant_ids.append(sub.tenant_id)
            logger.info(f"Trial expired for tenant {sub.tenant_id}")

        if suspended_tenant_ids:
            await db.flush()

        return suspended_tenant_ids

    async def check_past_due_subscriptions(self, db: AsyncSession) -> list[uuid.UUID]:
        """Suspend subscriptions that have exceeded the grace period."""
        today = date.today()
        stmt = select(TenantSubscription).where(
            TenantSubscription.status == SubscriptionStatus.PAST_DUE.value,
            TenantSubscription.grace_period_end < today,
        )
        result = await db.execute(stmt)
        past_due = list(result.scalars().all())

        suspended_ids = []
        for sub in past_due:
            sub.status = SubscriptionStatus.SUSPENDED.value
            suspended_ids.append(sub.tenant_id)

        if suspended_ids:
            await db.flush()

        return suspended_ids

    # ── Invoices ───────────────────────────────────────────────

    async def get_tenant_invoices(
        self, db: AsyncSession, tenant_id: uuid.UUID
    ) -> list[PlatformInvoice]:
        stmt = (
            select(PlatformInvoice)
            .where(PlatformInvoice.tenant_id == tenant_id)
            .order_by(PlatformInvoice.created_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_all_invoices(
        self, db: AsyncSession, page: int = 1, page_size: int = 20,
        status: str | None = None,
    ) -> tuple[list[PlatformInvoice], int]:
        """Get all platform invoices (super admin)."""
        stmt = select(PlatformInvoice)
        count_stmt = select(func.count(PlatformInvoice.id))

        if status:
            stmt = stmt.where(PlatformInvoice.status == status)
            count_stmt = count_stmt.where(PlatformInvoice.status == status)

        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(PlatformInvoice.created_at.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    # ── Revenue stats ──────────────────────────────────────────

    async def get_revenue_stats(self, db: AsyncSession) -> dict:
        """Get platform revenue statistics (super admin)."""
        today = date.today()
        month_start = today.replace(day=1)

        # Total revenue
        total_result = await db.execute(
            select(func.sum(PlatformInvoice.amount)).where(
                PlatformInvoice.status == PlatformInvoiceStatus.PAID.value
            )
        )
        total_revenue = total_result.scalar() or Decimal("0")

        # Monthly revenue
        monthly_result = await db.execute(
            select(func.sum(PlatformInvoice.amount)).where(
                PlatformInvoice.status == PlatformInvoiceStatus.PAID.value,
                PlatformInvoice.paid_at >= datetime(month_start.year, month_start.month, month_start.day, tzinfo=timezone.utc),
            )
        )
        monthly_revenue = monthly_result.scalar() or Decimal("0")

        # Subscription counts by status
        status_counts = {}
        for status in SubscriptionStatus:
            count = (
                await db.execute(
                    select(func.count(TenantSubscription.id)).where(
                        TenantSubscription.status == status.value
                    )
                )
            ).scalar() or 0
            status_counts[status.value.lower()] = count

        # MRR (monthly recurring revenue) = active subscriptions * plan price
        mrr_result = await db.execute(
            select(func.sum(SubscriptionPlan.price_monthly))
            .select_from(TenantSubscription)
            .join(SubscriptionPlan, TenantSubscription.plan_id == SubscriptionPlan.id)
            .where(TenantSubscription.status == SubscriptionStatus.ACTIVE.value)
        )
        mrr = mrr_result.scalar() or Decimal("0")

        return {
            "total_revenue": float(total_revenue),
            "monthly_revenue": float(monthly_revenue),
            "mrr": float(mrr),
            "subscriptions": status_counts,
            "total_tenants": sum(status_counts.values()),
        }

    # ── Feature sync ─────────────────────────────────────────

    async def sync_tenant_features(
        self, db: AsyncSession, tenant_id: uuid.UUID, plan: SubscriptionPlan
    ) -> None:
        """Copy plan feature flags to the tenant's settings.

        Features defined in the plan override the tenant's current feature
        settings.  Features *not* mentioned in the plan dict are left as-is,
        so only features explicitly listed in the plan are enforced.
        """
        if not plan.features:
            return

        tenant = await db.get(Tenant, tenant_id)
        if not tenant:
            return

        settings = dict(tenant.settings or {})
        features = dict(settings.get("features", {}))

        for key, enabled in plan.features.items():
            features[key] = enabled

        settings["features"] = features
        tenant.settings = settings
        await db.flush()

        logger.info(
            f"Synced features for tenant {tenant_id} from plan '{plan.name}': "
            f"{plan.features}"
        )

    # ── Auto-enroll legacy tenants ──────────────────────────

    async def auto_enroll_trial(
        self, db: AsyncSession, tenant_id: uuid.UUID
    ) -> TenantSubscription:
        """Auto-enroll a legacy tenant (no subscription) into a free trial.

        Uses the first active plan. Called from middleware when a tenant
        with no subscription record is detected.
        """
        plans = await self.list_plans(db)
        if not plans:
            raise ValueError("No subscription plans available for auto-enrollment")

        plan = plans[0]
        tenant = await db.get(Tenant, tenant_id)
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")

        sub = await self.start_trial(db, tenant_id, plan.id)
        logger.info(
            f"Auto-enrolled legacy tenant {tenant_id} ({tenant.name}) "
            f"into trial on plan '{plan.name}'"
        )
        return sub

    # ── Initialize subscription for new tenant ─────────────────

    async def initialize_tenant_subscription(
        self, db: AsyncSession, tenant_id: uuid.UUID, tenant_email: str,
        tenant_name: str, plan_id: uuid.UUID | None = None,
    ) -> TenantSubscription:
        """Set up a new tenant with a trial subscription.

        If no plan_id given, uses the first active plan.
        """
        if not plan_id:
            plans = await self.list_plans(db)
            if not plans:
                raise ValueError("No subscription plans available")
            plan_id = plans[0].id

        # Create Paystack customer if configured
        paystack = get_paystack_service()
        customer_code = None
        if paystack.is_configured:
            try:
                customer = await paystack.create_customer(
                    email=tenant_email,
                    first_name=tenant_name,
                    last_name="",
                    metadata={"tenant_id": str(tenant_id)},
                )
                customer_code = customer.get("customer_code")
            except Exception:
                logger.exception("Failed to create Paystack customer")

        sub = await self.start_trial(db, tenant_id, plan_id)
        if customer_code:
            sub.paystack_customer_code = customer_code
            await db.flush()

        return sub


def get_subscription_service() -> SubscriptionService:
    """Get subscription service instance."""
    return SubscriptionService()
