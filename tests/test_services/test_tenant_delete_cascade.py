"""Tenant delete must cancel subscriptions + detach WhatsApp logs.

Covers bugs #2 and #3 from the 2026-10-06 tester feedback:
tenant_subscriptions kept rendering soft-deleted tenants (and the
super admin could extend trial on them); WhatsApp conversations
kept bucketing their history under the dead tenant's name.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant, WhatsAppInboundMessage, WhatsAppOutboundMessage
from app.models.subscription import (
    BillingFrequency,
    SubscriptionPlan,
    SubscriptionStatus,
    TenantSubscription,
)
from app.services.tenant_service import TenantService


@pytest.mark.asyncio
async def test_delete_tenant_cancels_active_subscription(
    db: AsyncSession, test_tenant: Tenant
):
    """A TRIALING subscription on the deleted tenant must flip to CANCELLED."""
    plan = SubscriptionPlan(
        id=uuid.uuid4(),
        name=f"Test Plan {uuid.uuid4().hex[:6]}",
        price_monthly=100,
        price_annually=1000,
        currency="ZAR",
        max_students=50,
        max_staff=10,
        features={},
        trial_days=14,
        is_active=True,
    )
    db.add(plan)
    sub = TenantSubscription(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        plan_id=plan.id,
        status=SubscriptionStatus.TRIALING.value,
        billing_frequency=BillingFrequency.MONTHLY.value,
    )
    db.add(sub)
    await db.commit()

    await TenantService().delete_tenant(db, test_tenant.id)

    refreshed = (await db.execute(
        select(TenantSubscription).where(TenantSubscription.id == sub.id)
    )).scalar_one()
    assert refreshed.status == SubscriptionStatus.CANCELLED.value
    assert refreshed.cancelled_at is not None

    # Tenant itself should be soft-deleted.
    tenant = (await db.execute(
        select(Tenant).where(Tenant.id == test_tenant.id)
    )).scalar_one()
    assert tenant.deleted_at is not None
    assert tenant.is_active is False


@pytest.mark.asyncio
async def test_delete_tenant_leaves_cancelled_subscription_alone(
    db: AsyncSession, test_tenant: Tenant
):
    """Already-CANCELLED subs shouldn't get a fresh cancelled_at."""
    plan = SubscriptionPlan(
        id=uuid.uuid4(),
        name=f"Test Plan {uuid.uuid4().hex[:6]}",
        price_monthly=100,
        price_annually=1000,
        currency="ZAR",
        max_students=50,
        max_staff=10,
        features={},
        trial_days=14,
        is_active=True,
    )
    db.add(plan)
    old_cancelled = datetime(2026, 1, 1)
    sub = TenantSubscription(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        plan_id=plan.id,
        status=SubscriptionStatus.CANCELLED.value,
        billing_frequency=BillingFrequency.MONTHLY.value,
        cancelled_at=old_cancelled,
    )
    db.add(sub)
    await db.commit()

    await TenantService().delete_tenant(db, test_tenant.id)

    refreshed = (await db.execute(
        select(TenantSubscription).where(TenantSubscription.id == sub.id)
    )).scalar_one()
    assert refreshed.status == SubscriptionStatus.CANCELLED.value
    # Original cancelled_at preserved.
    assert refreshed.cancelled_at == old_cancelled


@pytest.mark.asyncio
async def test_delete_tenant_nulls_whatsapp_message_tenant_ids(
    db: AsyncSession, test_tenant: Tenant
):
    """WhatsApp conversation history must detach from the deleted tenant."""
    inb = WhatsAppInboundMessage(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        from_phone="27821234567",
        message_type="text",
        text="hello",
        meta_message_id=f"wamid.{uuid.uuid4().hex}",
        raw_payload={},
    )
    outb = WhatsAppOutboundMessage(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        to_phone="27821234567",
        message_type="text",
        body_text="hi back",
    )
    db.add_all([inb, outb])
    await db.commit()

    await TenantService().delete_tenant(db, test_tenant.id)

    refreshed_inb = (await db.execute(
        select(WhatsAppInboundMessage).where(WhatsAppInboundMessage.id == inb.id)
    )).scalar_one()
    refreshed_outb = (await db.execute(
        select(WhatsAppOutboundMessage).where(WhatsAppOutboundMessage.id == outb.id)
    )).scalar_one()
    assert refreshed_inb.tenant_id is None
    assert refreshed_outb.tenant_id is None

    # The rows themselves must survive — detachment, not deletion.
    assert refreshed_inb.text == "hello"
    assert refreshed_outb.body_text == "hi back"
