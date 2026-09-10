"""Tests for the BillingFrequency enum + subscription activation on
paid invoice — the pieces that make the "opt out of trial + choose
frequency" flow work.

We don't hit the initialize-payment HTTP endpoint here (needs a full
test app + DB); the tests below cover the pure-logic bits so the
happy path (activate subscription when a paid invoice's webhook
lands) is protected.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.models.subscription import (
    BillingFrequency, PlatformInvoiceStatus, SubscriptionStatus,
)
from app.services import gateway_service


class TestBillingFrequencyEnum:
    def test_monthly_period_is_30_days(self):
        assert BillingFrequency.MONTHLY.period_days == 30

    def test_annual_period_is_365_days(self):
        assert BillingFrequency.ANNUALLY.period_days == 365

    def test_values_match_strings(self):
        """String enum values are what get stored in the DB column;
        pinning them prevents accidental renames breaking migrations."""
        assert BillingFrequency.MONTHLY.value == "MONTHLY"
        assert BillingFrequency.ANNUALLY.value == "ANNUALLY"

    def test_parse_from_string(self):
        assert BillingFrequency("MONTHLY") is BillingFrequency.MONTHLY
        assert BillingFrequency("ANNUALLY") is BillingFrequency.ANNUALLY

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            BillingFrequency("QUARTERLY")


class TestSubscriptionActivationOnPaidInvoice:
    """When a payment webhook lands and marks an invoice PAID, the
    linked TenantSubscription must flip TRIALING → ACTIVE with the
    invoice's coverage window as its new current_period. Without this
    the tenant paid but stays trialing — which is exactly the bug we
    just fixed."""

    async def test_paid_invoice_activates_subscription(self):
        """apply_payment_event on a succeeded event runs the
        activation helper. Using mocks so no DB is required."""
        invoice_id = uuid4()
        subscription_id = uuid4()
        tenant_id = uuid4()

        # Fake invoice — PENDING, becomes PAID
        invoice = MagicMock()
        invoice.id = invoice_id
        invoice.subscription_id = subscription_id
        invoice.status = PlatformInvoiceStatus.PENDING.value
        invoice.billing_period_start = date(2026, 9, 10)
        invoice.billing_period_end = date(2026, 10, 10)

        # Fake subscription — starts TRIALING, should end ACTIVE
        sub = MagicMock()
        sub.id = subscription_id
        sub.tenant_id = tenant_id
        sub.plan_id = uuid4()
        sub.status = SubscriptionStatus.TRIALING.value
        sub.current_period_start = None
        sub.current_period_end = None
        sub.failed_payment_count = 3
        sub.grace_period_end = date(2026, 9, 20)

        db = AsyncMock()
        # db.get called twice: first for the invoice, then for the sub,
        # then possibly for the plan. Sequence the return values.
        db.get.side_effect = [invoice, sub, MagicMock(name="plan")]
        db.flush = AsyncMock()

        # sync_tenant_features is called after activation — mock the
        # whole service to prove activation happened WITHOUT needing
        # the real feature-syncing machinery.
        with patch(
            "app.services.subscription_service.get_subscription_service"
        ) as mock_svc:
            mock_svc.return_value.sync_tenant_features = AsyncMock()

            event = gateway_service.PaymentEvent(
                invoice_id=invoice_id,
                succeeded=True,
                provider_reference="paynow-ref-123",
                payment_method="paynow_ecocash",
                failure_reason=None,
                raw={},
            )
            result = await gateway_service.apply_payment_event(db, event)

        assert result is invoice
        assert invoice.status == PlatformInvoiceStatus.PAID.value
        # Activation happened — the whole point of this test
        assert sub.status == SubscriptionStatus.ACTIVE.value
        assert sub.current_period_start == date(2026, 9, 10)
        assert sub.current_period_end == date(2026, 10, 10)
        assert sub.failed_payment_count == 0
        assert sub.grace_period_end is None

    async def test_duplicate_webhook_is_noop_and_doesnt_reactivate(self):
        """PayNow retries up to 10 times if we return non-2xx. An
        already-PAID invoice must not trigger a second activation (which
        would reset the period window incorrectly)."""
        invoice = MagicMock()
        invoice.id = uuid4()
        invoice.subscription_id = uuid4()
        invoice.status = PlatformInvoiceStatus.PAID.value  # already paid

        db = AsyncMock()
        db.get = AsyncMock(return_value=invoice)
        db.flush = AsyncMock()

        with patch(
            "app.services.subscription_service.get_subscription_service"
        ) as mock_svc:
            mock_svc.return_value.sync_tenant_features = AsyncMock()

            event = gateway_service.PaymentEvent(
                invoice_id=invoice.id, succeeded=True,
                provider_reference="dup", payment_method="paynow_visa",
                failure_reason=None, raw={},
            )
            await gateway_service.apply_payment_event(db, event)

        # sync_tenant_features must NOT have been called on a dup —
        # activation would incorrectly re-fire otherwise.
        assert mock_svc.return_value.sync_tenant_features.call_count == 0

    async def test_activation_failure_doesnt_lose_the_payment(self):
        """The payment status is authoritative — if the follow-on
        subscription-activation code crashes (bad plan_id, DB race,
        whatever), the invoice must still be PAID. Otherwise a
        retriable webhook rolls back the payment record too."""
        invoice = MagicMock()
        invoice.id = uuid4()
        invoice.subscription_id = uuid4()
        invoice.status = PlatformInvoiceStatus.PENDING.value
        invoice.billing_period_start = date(2026, 9, 10)
        invoice.billing_period_end = date(2026, 10, 10)

        db = AsyncMock()
        # First .get returns the invoice; second (in the activation helper)
        # blows up.
        call_count = 0
        async def flaky_get(model, obj_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return invoice
            raise RuntimeError("simulated DB failure inside activation")
        db.get = flaky_get
        db.flush = AsyncMock()

        event = gateway_service.PaymentEvent(
            invoice_id=invoice.id, succeeded=True,
            provider_reference="ok", payment_method="paynow_ecocash",
            failure_reason=None, raw={},
        )
        # Must NOT raise — payment stays recorded.
        result = await gateway_service.apply_payment_event(db, event)
        assert result is invoice
        assert invoice.status == PlatformInvoiceStatus.PAID.value

    async def test_failed_event_does_not_activate(self):
        """A failed payment leaves the invoice PENDING (tenant can retry)
        and must not activate the subscription."""
        invoice = MagicMock()
        invoice.id = uuid4()
        invoice.subscription_id = uuid4()
        invoice.status = PlatformInvoiceStatus.PENDING.value

        db = AsyncMock()
        db.get = AsyncMock(return_value=invoice)
        db.flush = AsyncMock()

        with patch(
            "app.services.subscription_service.get_subscription_service"
        ) as mock_svc:
            mock_svc.return_value.sync_tenant_features = AsyncMock()

            event = gateway_service.PaymentEvent(
                invoice_id=invoice.id, succeeded=False,
                provider_reference=None, payment_method="paynow_visa",
                failure_reason="Cancelled by user", raw={},
            )
            await gateway_service.apply_payment_event(db, event)

        # No activation should have happened
        assert mock_svc.return_value.sync_tenant_features.call_count == 0
        # Invoice stays PENDING so the tenant can try again
        assert invoice.status == PlatformInvoiceStatus.PENDING.value
