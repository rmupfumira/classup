"""Tests for the payment-gateway abstraction + Yoco provider.

Live API calls are mocked (httpx + webhook signatures are deterministic
once you control the secret). What we lock down:

  - Config CRUD with masked-secret round-trip
  - Provider registry + list_providers
  - Yoco webhook signature: valid signature passes, tampering fails,
    stale timestamp rejected
  - Yoco event parser pulls invoice_id from metadata
  - apply_payment_event marks PlatformInvoice PAID idempotently
  - PaynowProvider stubs raise NotImplementedError (intentional)
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformInvoice, SystemSettings, Tenant
from app.models.subscription import (
    PlatformInvoiceStatus,
    SubscriptionPlan,
    SubscriptionStatus,
    TenantSubscription,
)
from app.services import gateway_service
from app.services.gateway_service import (
    PROVIDER_REGISTRY,
    GatewayConfig,
    MASKED,
    PayNowProvider,
    PaymentEvent,
    YocoProvider,
    apply_payment_event,
    get_active_provider,
    get_config,
    list_providers,
    save_config,
)


@pytest.fixture(autouse=True)
async def _reset_gateway_row(db: AsyncSession):
    await db.execute(
        SystemSettings.__table__.delete().where(
            SystemSettings.key == gateway_service.GATEWAY_SETTINGS_KEY
        )
    )
    await db.commit()
    yield


# Sample valid Yoco webhook secret (base64-encoded 32 random bytes,
# prefixed with "whsec_" as Yoco does in its dashboard)
_SECRET_BYTES = b"this-is-32-bytes-of-test-secret!"
_VALID_SECRET = "whsec_" + base64.b64encode(_SECRET_BYTES).decode("ascii")


def _sign_yoco(msg_id: str, timestamp: str, body: bytes) -> str:
    """Produce a valid Yoco webhook signature for the given inputs."""
    signed = f"{msg_id}.{timestamp}.{body.decode('utf-8')}"
    digest = hmac.new(_SECRET_BYTES, signed.encode("utf-8"), hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


class TestProviderRegistry:
    def test_yoco_and_paynow_registered(self):
        assert "yoco" in PROVIDER_REGISTRY
        assert "paynow" in PROVIDER_REGISTRY
        assert PROVIDER_REGISTRY["yoco"] is YocoProvider
        assert PROVIDER_REGISTRY["paynow"] is PayNowProvider

    def test_list_providers_returns_metadata(self):
        items = list_providers()
        assert {p["provider_id"] for p in items} >= {"yoco", "paynow"}
        yoco = next(p for p in items if p["provider_id"] == "yoco")
        assert yoco["display_name"] == "Yoco"
        assert any(f["key"] == "secret_key" for f in yoco["credential_fields"])
        assert any(f["key"] == "webhook_secret" for f in yoco["credential_fields"])


class TestConfigStorage:
    async def test_get_returns_empty_when_no_row(self, db: AsyncSession):
        cfg = await get_config(db)
        assert cfg.provider_id == ""
        assert cfg.is_enabled is False
        assert cfg.configured is False

    async def test_save_persists(self, db: AsyncSession):
        await save_config(
            db,
            provider_id="yoco",
            is_enabled=True,
            credentials={"secret_key": "sk_live_xyz", "webhook_secret": "whsec_abc"},
        )
        await db.commit()
        cfg = await get_config(db)
        assert cfg.provider_id == "yoco"
        assert cfg.is_enabled is True
        assert cfg.credentials["secret_key"] == "sk_live_xyz"
        assert cfg.configured is True

    async def test_masked_round_trip_preserves_secret(self, db: AsyncSession):
        # Initial save with real secret
        await save_config(
            db,
            provider_id="yoco",
            is_enabled=True,
            credentials={"secret_key": "sk_real_xyz", "webhook_secret": "real_whsec"},
        )
        await db.commit()

        # Re-save with MASKED in place of the secret (UI does this when user
        # leaves the field unchanged on the form)
        await save_config(
            db,
            provider_id="yoco",
            is_enabled=False,  # paused
            credentials={"secret_key": MASKED, "webhook_secret": MASKED},
        )
        await db.commit()

        cfg = await get_config(db)
        assert cfg.credentials["secret_key"] == "sk_real_xyz"
        assert cfg.credentials["webhook_secret"] == "real_whsec"
        assert cfg.is_enabled is False  # toggled paused but creds preserved

    async def test_switching_provider_keeps_per_provider_creds_clean(self, db: AsyncSession):
        await save_config(
            db, provider_id="yoco", is_enabled=True,
            credentials={"secret_key": "sk_yoco"},
        )
        await db.commit()
        # Switch to paynow — should overwrite, not merge
        await save_config(
            db, provider_id="paynow", is_enabled=True,
            credentials={"integration_id": "1234", "integration_key": "key"},
        )
        await db.commit()
        cfg = await get_config(db)
        assert cfg.provider_id == "paynow"
        assert "secret_key" not in cfg.credentials  # yoco creds wiped on switch

    async def test_unknown_provider_rejected(self, db: AsyncSession):
        with pytest.raises(ValueError, match="Unknown provider"):
            await save_config(
                db, provider_id="lolnope", is_enabled=True, credentials={}
            )

    async def test_get_active_provider_returns_instance(self, db: AsyncSession):
        await save_config(
            db, provider_id="yoco", is_enabled=True,
            credentials={"secret_key": "sk_x", "webhook_secret": "ws"},
        )
        await db.commit()
        provider = await get_active_provider(db)
        assert provider is not None
        assert isinstance(provider, YocoProvider)
        assert provider.secret_key == "sk_x"

    async def test_get_active_provider_returns_none_when_disabled(self, db: AsyncSession):
        await save_config(
            db, provider_id="yoco", is_enabled=False,
            credentials={"secret_key": "sk_x"},
        )
        await db.commit()
        assert await get_active_provider(db) is None

    async def test_masked_secrets_in_get_response(self, db: AsyncSession):
        await save_config(
            db, provider_id="yoco", is_enabled=True,
            credentials={"secret_key": "sk_real", "webhook_secret": "real_whsec"},
        )
        await db.commit()
        cfg = await get_config(db)
        masked = cfg.with_masked_secrets()
        assert masked["credentials"]["secret_key"] == MASKED
        assert masked["credentials"]["webhook_secret"] == MASKED


class TestYocoWebhookSignature:
    """Yoco signatures use Standard Webhooks spec. Bad sig → reject."""

    def _make_provider(self) -> YocoProvider:
        return YocoProvider({"secret_key": "sk_test", "webhook_secret": _VALID_SECRET})

    def test_valid_signature_accepted(self):
        provider = self._make_provider()
        body = b'{"type":"payment.succeeded","payload":{}}'
        ts = str(int(time.time()))
        msg_id = "msg_abc123"
        sig = _sign_yoco(msg_id, ts, body)
        headers = {
            "webhook-id": msg_id,
            "webhook-timestamp": ts,
            "webhook-signature": sig,
        }
        assert provider.verify_webhook(headers, body) is True

    def test_tampered_body_rejected(self):
        provider = self._make_provider()
        body = b'{"type":"payment.succeeded","payload":{}}'
        ts = str(int(time.time()))
        msg_id = "msg_abc123"
        sig = _sign_yoco(msg_id, ts, body)
        headers = {
            "webhook-id": msg_id,
            "webhook-timestamp": ts,
            "webhook-signature": sig,
        }
        tampered_body = body.replace(b"succeeded", b"failed")
        assert provider.verify_webhook(headers, tampered_body) is False

    def test_old_timestamp_rejected(self):
        provider = self._make_provider()
        body = b'{"type":"payment.succeeded","payload":{}}'
        old_ts = str(int(time.time()) - 600)  # 10 minutes old
        msg_id = "msg_abc123"
        sig = _sign_yoco(msg_id, old_ts, body)
        headers = {
            "webhook-id": msg_id,
            "webhook-timestamp": old_ts,
            "webhook-signature": sig,
        }
        assert provider.verify_webhook(headers, body) is False

    def test_missing_headers_rejected(self):
        provider = self._make_provider()
        assert provider.verify_webhook({}, b"{}") is False

    def test_unsigned_when_no_secret_rejected(self):
        provider = YocoProvider({"secret_key": "sk", "webhook_secret": ""})
        body = b'{"type":"payment.succeeded","payload":{}}'
        ts = str(int(time.time()))
        sig = _sign_yoco("m", ts, body)
        headers = {"webhook-id": "m", "webhook-timestamp": ts, "webhook-signature": sig}
        assert provider.verify_webhook(headers, body) is False


class TestYocoEventParser:
    def test_succeeded_event_with_metadata(self):
        provider = YocoProvider({"secret_key": "sk", "webhook_secret": _VALID_SECRET})
        invoice_id = uuid.uuid4()
        body = json.dumps({
            "type": "payment.succeeded",
            "payload": {
                "id": "pmt_abc123",
                "metadata": {"invoice_id": str(invoice_id), "tenant_id": "tenant-1"},
            },
        }).encode("utf-8")
        event = provider.parse_webhook_event(body)
        assert event.succeeded is True
        assert event.invoice_id == invoice_id
        assert event.provider_reference == "pmt_abc123"
        assert event.payment_method == "yoco_card"

    def test_failed_event_keeps_reason(self):
        provider = YocoProvider({"secret_key": "sk", "webhook_secret": _VALID_SECRET})
        body = json.dumps({
            "type": "payment.failed",
            "payload": {
                "id": "pmt_xyz",
                "statusMessage": "Card declined",
                "metadata": {"invoice_id": str(uuid.uuid4())},
            },
        }).encode("utf-8")
        event = provider.parse_webhook_event(body)
        assert event.succeeded is False
        assert event.failure_reason == "Card declined"

    def test_garbled_body_returns_empty_event(self):
        provider = YocoProvider({"secret_key": "sk", "webhook_secret": _VALID_SECRET})
        event = provider.parse_webhook_event(b"not json")
        assert event.succeeded is False
        assert event.invoice_id is None
        assert event.failure_reason


class TestApplyPaymentEvent:
    """The webhook → PlatformInvoice plumbing."""

    @pytest.fixture
    async def _platform_invoice(
        self, db: AsyncSession, test_tenant: Tenant, test_admin
    ) -> PlatformInvoice:
        # Need a SubscriptionPlan + TenantSubscription to create an invoice
        plan = SubscriptionPlan(
            id=uuid.uuid4(),
            name="Test Plan",
            description="",
            price_monthly=Decimal("100.00"),
            price_annually=None,
            currency="ZAR",
            max_students=10,
            max_staff=5,
            trial_days=14,
            is_active=True,
            display_order=1,
            features={},
        )
        db.add(plan)
        sub = TenantSubscription(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            plan_id=plan.id,
            status=SubscriptionStatus.TRIALING.value,
            trial_start=date.today(),
            trial_end=date.today(),
            current_period_start=date.today(),
            current_period_end=date.today(),
        )
        db.add(sub)
        await db.flush()

        inv = PlatformInvoice(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            subscription_id=sub.id,
            amount=Decimal("100.00"),
            currency="ZAR",
            status=PlatformInvoiceStatus.PENDING.value,
            billing_period_start=date.today(),
            billing_period_end=date.today(),
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)
        return inv

    async def test_successful_event_marks_paid(
        self, db: AsyncSession, _platform_invoice: PlatformInvoice
    ):
        event = PaymentEvent(
            invoice_id=_platform_invoice.id, succeeded=True,
            provider_reference="pmt_abc", payment_method="yoco_card",
            failure_reason=None, raw={},
        )
        result = await apply_payment_event(db, event)
        await db.commit()
        assert result is not None
        await db.refresh(_platform_invoice)
        assert _platform_invoice.status == PlatformInvoiceStatus.PAID.value
        assert _platform_invoice.paid_at is not None
        assert _platform_invoice.payment_method == "yoco_card"
        assert _platform_invoice.paystack_reference == "pmt_abc"

    async def test_duplicate_event_is_idempotent(
        self, db: AsyncSession, _platform_invoice: PlatformInvoice
    ):
        event = PaymentEvent(
            invoice_id=_platform_invoice.id, succeeded=True,
            provider_reference="pmt_abc", payment_method="yoco_card",
            failure_reason=None, raw={},
        )
        await apply_payment_event(db, event)
        await db.commit()
        first_paid_at = _platform_invoice.paid_at

        # Fire again — should not crash, should not change paid_at
        await apply_payment_event(db, event)
        await db.commit()
        await db.refresh(_platform_invoice)
        assert _platform_invoice.paid_at == first_paid_at  # unchanged

    async def test_event_with_unknown_invoice_id_returns_none(self, db: AsyncSession):
        event = PaymentEvent(
            invoice_id=uuid.uuid4(),  # not in DB
            succeeded=True, provider_reference="r", payment_method="m",
            failure_reason=None, raw={},
        )
        assert await apply_payment_event(db, event) is None


class TestPayNowRegistered:
    """Paynow is fully implemented for platform subscription billing.
    Detailed tests live in tests/test_services/test_paynow_provider.py —
    here we just assert the provider is registered + surfaces itself in
    the admin dropdown."""

    def test_paynow_in_registry(self):
        from app.services.gateway_service import PROVIDER_REGISTRY
        assert "paynow" in PROVIDER_REGISTRY
        assert PROVIDER_REGISTRY["paynow"] is PayNowProvider

    def test_paynow_appears_in_admin_list(self):
        from app.services.gateway_service import list_providers
        providers = {p["provider_id"]: p for p in list_providers()}
        assert "paynow" in providers
        assert providers["paynow"]["display_name"] == "Paynow (Zimbabwe)"
        cred_keys = {f["key"] for f in providers["paynow"]["credential_fields"]}
        assert cred_keys == {"integration_id", "integration_key", "test_mode"}
