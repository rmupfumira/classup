"""Payment-gateway abstraction for platform subscription billing.

Each ClassUp deployment is country-specific and configures ONE gateway
(plus the manual EFT-with-POP fallback that always works). The super
admin picks the provider on /admin/payment-gateways, enters credentials,
and from then on tenants can pay platform invoices by card on the
/subscription page.

Adding a new provider:
  1. Subclass `PaymentProvider`
  2. Implement `create_checkout`, `verify_webhook`, `parse_webhook_event`
  3. Register in `PROVIDER_REGISTRY`
  4. Add a webhook route in app/api/v1/payment_webhooks.py
  5. Add a credentials section to the admin template

Storage: a single row in system_settings.payment_gateway_config holds
the active provider id + its credentials. Secrets are masked in API
responses (existing pattern from email_settings). No row = no gateway
configured = EFT-with-POP remains the only option.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings as get_app_settings
from app.models import PlatformInvoice, SystemSettings

logger = logging.getLogger(__name__)

GATEWAY_SETTINGS_KEY = "payment_gateway_config"
MASKED = "********"  # what we send back in GET responses instead of real secrets


# ============================================================================
# Result + event types — provider-agnostic
# ============================================================================

@dataclass(frozen=True)
class CheckoutResult:
    """What create_checkout returns. The page redirects to redirect_url."""
    redirect_url: str
    reference: str  # the provider's id for this checkout — stored for reconciliation


@dataclass(frozen=True)
class PaymentEvent:
    """Normalised result of parse_webhook_event. The webhook route only
    cares about the invoice + whether it succeeded."""
    invoice_id: UUID | None  # comes from the metadata we set during create_checkout
    succeeded: bool
    provider_reference: str | None  # provider's transaction/charge id
    payment_method: str | None
    failure_reason: str | None
    raw: dict  # full payload for debugging


# ============================================================================
# Base class
# ============================================================================

class PaymentProvider(ABC):
    """Subclass to add a new gateway."""

    provider_id: str = ""       # short slug, e.g. "yoco" — stable across versions
    display_name: str = ""      # human-readable, shown on admin page
    description: str = ""       # one-line market positioning, also on admin page
    credential_fields: list[dict] = []  # rendered as a form on the admin page
                                        # [{"key": "secret_key", "label": "Secret key", "type": "password", "help": "..."}]

    def __init__(self, credentials: dict[str, Any]):
        self.credentials = credentials or {}

    # ────────────────────── Abstract methods ──────────────────────

    @abstractmethod
    async def create_checkout(
        self,
        invoice: PlatformInvoice,
        *,
        return_url: str,
        cancel_url: str,
    ) -> CheckoutResult:
        """Create a hosted-checkout session and return its redirect URL.

        ``invoice`` already has currency, amount, id — provider implementations
        translate to whatever the gateway's API wants. ``return_url`` is where
        the user lands after a successful payment; ``cancel_url`` when they
        bail out of the gateway page.
        """

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Confirm the webhook actually came from the gateway (HMAC etc)."""

    @abstractmethod
    def parse_webhook_event(self, body: bytes) -> PaymentEvent:
        """Decode the body into our normalised PaymentEvent."""

    async def test_credentials(self) -> tuple[bool, str]:
        """Optional: send a low-impact request to verify credentials.
        Override per-provider — default does nothing useful."""
        return False, "Provider has no test endpoint implemented."


# ============================================================================
# Yoco — South African gateway (primary card processor for SMBs)
# ============================================================================

class YocoProvider(PaymentProvider):
    """Yoco Online Payments via the Checkouts API.

    Docs: https://developer.yoco.com/online-payments

    Webhook flow:
      - Yoco POSTs to our webhook URL
      - Headers include `webhook-id`, `webhook-timestamp`, `webhook-signature`
      - Signature is base64(HMAC-SHA256(secret, f"{id}.{timestamp}.{body}"))
      - We verify, then look at payload.type — `payment.succeeded` / `payment.failed`
      - payload.metadata.invoice_id tells us which PlatformInvoice to mark paid
    """

    provider_id = "yoco"
    display_name = "Yoco"
    description = "South African card payments (cards, instant EFT, mobile wallets)."
    credential_fields = [
        {"key": "secret_key", "label": "Secret key", "type": "password",
         "placeholder": "sk_live_...",
         "help": "Found in Yoco Business Portal → Developer → API Keys. Starts with sk_live_ or sk_test_."},
        {"key": "webhook_secret", "label": "Webhook secret", "type": "password",
         "placeholder": "whsec_...",
         "help": "Generated when you register the webhook URL in Yoco's portal. Used to verify incoming payment events."},
    ]

    API_BASE = "https://payments.yoco.com/api"

    @property
    def secret_key(self) -> str:
        return str(self.credentials.get("secret_key", "")).strip()

    @property
    def webhook_secret(self) -> str:
        return str(self.credentials.get("webhook_secret", "")).strip()

    async def create_checkout(
        self,
        invoice: PlatformInvoice,
        *,
        return_url: str,
        cancel_url: str,
    ) -> CheckoutResult:
        if not self.secret_key:
            raise RuntimeError("Yoco secret_key is not configured.")

        # Yoco wants amount in the smallest currency unit (cents for ZAR)
        amount_cents = int((Decimal(invoice.amount) * 100).quantize(Decimal("1")))

        payload = {
            "amount": amount_cents,
            "currency": (invoice.currency or "ZAR").upper(),
            "successUrl": return_url,
            "cancelUrl": cancel_url,
            "failureUrl": cancel_url,
            "metadata": {
                # Yoco echoes this back in the webhook — that's how we tie
                # the payment back to our PlatformInvoice
                "invoice_id": str(invoice.id),
                "tenant_id": str(invoice.tenant_id),
            },
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.API_BASE}/checkouts",
                headers={
                    "Authorization": f"Bearer {self.secret_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code >= 300:
            logger.warning(
                f"Yoco checkout creation failed: {resp.status_code} {resp.text[:200]}"
            )
            raise RuntimeError(
                f"Yoco rejected the checkout request: {resp.status_code}"
            )

        data = resp.json()
        return CheckoutResult(
            redirect_url=data["redirectUrl"],
            reference=data["id"],
        )

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Yoco signatures use Standard Webhooks spec."""
        if not self.webhook_secret:
            logger.warning("Yoco webhook_secret not configured — refusing event")
            return False

        # Headers may be Webhook-Id or webhook-id depending on transit
        h = {k.lower(): v for k, v in headers.items()}
        msg_id = h.get("webhook-id")
        timestamp = h.get("webhook-timestamp")
        sig_header = h.get("webhook-signature")
        if not (msg_id and timestamp and sig_header):
            logger.warning("Yoco webhook missing signature headers")
            return False

        # Anti-replay: reject if older than 5 minutes
        try:
            ts = int(timestamp)
            if abs(time.time() - ts) > 300:
                logger.warning(f"Yoco webhook timestamp too old/future: {ts}")
                return False
        except (ValueError, TypeError):
            return False

        # The secret format is "whsec_<base64-encoded-bytes>" — strip prefix + decode
        secret = self.webhook_secret
        if secret.startswith("whsec_"):
            secret = secret[len("whsec_"):]
        try:
            secret_bytes = base64.b64decode(secret)
        except Exception:
            logger.warning("Yoco webhook secret is not valid base64")
            return False

        signed_content = f"{msg_id}.{timestamp}.{body.decode('utf-8', errors='replace')}"
        expected = base64.b64encode(
            hmac.new(secret_bytes, signed_content.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii")

        # webhook-signature is space-separated `v1,<sig>` pairs — match any
        for part in sig_header.split(" "):
            if "," not in part:
                continue
            version, sig = part.split(",", 1)
            if version == "v1" and hmac.compare_digest(sig, expected):
                return True
        logger.warning("Yoco webhook signature mismatch")
        return False

    def parse_webhook_event(self, body: bytes) -> PaymentEvent:
        try:
            data = json.loads(body)
        except Exception:
            return PaymentEvent(
                invoice_id=None, succeeded=False,
                provider_reference=None, payment_method=None,
                failure_reason="Could not parse webhook JSON", raw={},
            )

        event_type = data.get("type", "")
        payload = data.get("payload") or {}
        metadata = payload.get("metadata") or {}
        invoice_str = metadata.get("invoice_id")
        invoice_id: UUID | None = None
        try:
            if invoice_str:
                invoice_id = UUID(invoice_str)
        except ValueError:
            invoice_id = None

        succeeded = event_type == "payment.succeeded"
        return PaymentEvent(
            invoice_id=invoice_id,
            succeeded=succeeded,
            provider_reference=str(payload.get("id") or ""),
            payment_method="yoco_card",
            failure_reason=(payload.get("statusMessage") if not succeeded else None),
            raw=data,
        )

    async def test_credentials(self) -> tuple[bool, str]:
        """Yoco's API has no dedicated /me endpoint, but creating a checkout
        with $0.01 against a throwaway URL will return 401 if the key is bad
        without actually charging anything. We make a *minimal* call here."""
        if not self.secret_key:
            return False, "No secret key configured."
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Yoco won't accept amount=0 but will validate the auth header
            # before checking the body, so a 400 response with auth OK still
            # tells us creds are valid.
            try:
                resp = await client.post(
                    f"{self.API_BASE}/checkouts",
                    headers={"Authorization": f"Bearer {self.secret_key}"},
                    json={},  # deliberately invalid; we want to see 400 vs 401
                )
            except httpx.HTTPError as e:
                return False, f"Could not reach Yoco: {e}"
        if resp.status_code == 401:
            return False, "Secret key rejected — check the value in Yoco Business Portal."
        if resp.status_code in (400, 422):
            return True, "Secret key looks valid."
        if resp.status_code in (200, 201):
            return True, "Connection OK."
        return False, f"Unexpected Yoco response: {resp.status_code}"


# ============================================================================
# PayNow — Zimbabwe gateway (scaffolded; finalise when Zim instance deploys)
# ============================================================================

class PayNowProvider(PaymentProvider):
    """Zimbabwe's Paynow gateway. Filled in when the Zim instance is being
    provisioned — the abstraction + admin UI is ready to go.

    Docs: https://developers.paynow.co.zw
    Auth: Integration ID + Integration Key, with HMAC-MD5 (yes, MD5) on a
    pipe-separated, key-sorted payload. Webhook ("result URL") delivery is
    by POST with the same hash style.
    """

    provider_id = "paynow"
    display_name = "Paynow (Zimbabwe)"
    description = "EcoCash, OneMoney, ZIPIT, Visa/Mastercard for Zimbabwe."
    credential_fields = [
        {"key": "integration_id", "label": "Integration ID", "type": "text",
         "placeholder": "e.g. 12345",
         "help": "From Paynow → Sellers Dashboard → Receive Payments → 3rd Party API."},
        {"key": "integration_key", "label": "Integration Key", "type": "password",
         "placeholder": "GUID",
         "help": "Generated alongside the Integration ID. Used to sign + verify every call."},
    ]

    async def create_checkout(
        self,
        invoice: PlatformInvoice,
        *,
        return_url: str,
        cancel_url: str,
    ) -> CheckoutResult:
        raise NotImplementedError(
            "Paynow integration is scaffolded but not wired up yet — "
            "will be completed when the Zimbabwe instance is being deployed."
        )

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        raise NotImplementedError("Paynow webhook verification not yet implemented.")

    def parse_webhook_event(self, body: bytes) -> PaymentEvent:
        raise NotImplementedError("Paynow event parsing not yet implemented.")


# ============================================================================
# Registry — wires `provider_id` strings to classes
# ============================================================================

PROVIDER_REGISTRY: dict[str, type[PaymentProvider]] = {
    YocoProvider.provider_id: YocoProvider,
    PayNowProvider.provider_id: PayNowProvider,
}


def list_providers() -> list[dict[str, Any]]:
    """For the admin UI dropdown — provider id + display name + credential
    fields so the form renders dynamically."""
    return [
        {
            "provider_id": cls.provider_id,
            "display_name": cls.display_name,
            "description": cls.description,
            "credential_fields": cls.credential_fields,
        }
        for cls in PROVIDER_REGISTRY.values()
    ]


# ============================================================================
# Config storage in system_settings.payment_gateway_config
# ============================================================================

@dataclass(frozen=True)
class GatewayConfig:
    provider_id: str       # "" means none configured
    is_enabled: bool       # admin can pause without deleting credentials
    credentials: dict[str, Any]

    @property
    def configured(self) -> bool:
        return bool(self.provider_id) and self.is_enabled

    def with_masked_secrets(self) -> dict[str, Any]:
        """Build a dict safe to return from a GET endpoint."""
        cls = PROVIDER_REGISTRY.get(self.provider_id)
        masked = {}
        if cls:
            for field in cls.credential_fields:
                key = field["key"]
                value = self.credentials.get(key, "")
                if field.get("type") == "password" and value:
                    masked[key] = MASKED
                else:
                    masked[key] = value
        return {
            "provider_id": self.provider_id,
            "is_enabled": self.is_enabled,
            "credentials": masked,
        }


async def get_config(db: AsyncSession) -> GatewayConfig:
    """Read the current gateway config. Returns an empty config if no row
    exists — callers treat that as "EFT-only mode"."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == GATEWAY_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if not row or not row.value:
        return GatewayConfig(provider_id="", is_enabled=False, credentials={})
    v = row.value or {}
    return GatewayConfig(
        provider_id=str(v.get("provider_id", "")),
        is_enabled=bool(v.get("is_enabled", False)),
        credentials=dict(v.get("credentials") or {}),
    )


async def save_config(
    db: AsyncSession,
    *,
    provider_id: str,
    is_enabled: bool,
    credentials: dict[str, Any],
) -> GatewayConfig:
    """Upsert the gateway config. If the admin sent back ``MASKED`` for a
    secret, preserve the existing value (matches the email_settings pattern)."""
    if provider_id and provider_id not in PROVIDER_REGISTRY:
        raise ValueError(f"Unknown provider: {provider_id}")

    existing = await get_config(db)
    merged_creds = dict(credentials or {})
    if existing.provider_id == provider_id:
        for key, value in (credentials or {}).items():
            if value == MASKED:
                merged_creds[key] = existing.credentials.get(key, "")

    new_value = {
        "provider_id": provider_id,
        "is_enabled": is_enabled,
        "credentials": merged_creds,
    }

    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == GATEWAY_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = new_value
    else:
        db.add(SystemSettings(key=GATEWAY_SETTINGS_KEY, value=new_value))
    await db.flush()
    logger.info(
        f"Payment gateway config saved: provider={provider_id} enabled={is_enabled}"
    )
    return GatewayConfig(
        provider_id=provider_id,
        is_enabled=is_enabled,
        credentials=merged_creds,
    )


async def get_active_provider(db: AsyncSession) -> PaymentProvider | None:
    """Return an instantiated provider if the platform is configured and
    enabled, otherwise None."""
    cfg = await get_config(db)
    if not cfg.configured:
        return None
    cls = PROVIDER_REGISTRY.get(cfg.provider_id)
    if not cls:
        return None
    return cls(cfg.credentials)


async def test_config(
    db: AsyncSession,
    *,
    provider_id: str,
    credentials: dict[str, Any],
) -> tuple[bool, str]:
    """Verify credentials without saving them. ``credentials`` may contain
    MASKED placeholders — we hydrate them from the saved config first."""
    cls = PROVIDER_REGISTRY.get(provider_id)
    if not cls:
        return False, f"Unknown provider: {provider_id}"
    existing = await get_config(db)
    hydrated = dict(credentials or {})
    if existing.provider_id == provider_id:
        for key, value in hydrated.items():
            if value == MASKED:
                hydrated[key] = existing.credentials.get(key, "")
    provider = cls(hydrated)
    return await provider.test_credentials()


# ============================================================================
# Apply a webhook event to a PlatformInvoice
# ============================================================================

async def apply_payment_event(
    db: AsyncSession, event: PaymentEvent
) -> PlatformInvoice | None:
    """Mark the invoice paid / failed based on the parsed event.

    Idempotent: if the invoice is already PAID, this is a no-op (handles
    duplicate webhook deliveries which every gateway does sometimes).
    """
    if not event.invoice_id:
        logger.warning("Payment event without invoice_id metadata, ignored")
        return None

    invoice = await db.get(PlatformInvoice, event.invoice_id)
    if not invoice:
        logger.warning(f"Payment event for unknown invoice {event.invoice_id}")
        return None

    from app.models.subscription import PlatformInvoiceStatus

    if event.succeeded:
        if invoice.status == PlatformInvoiceStatus.PAID.value:
            logger.info(f"Invoice {invoice.id} already PAID — duplicate webhook")
            return invoice
        from datetime import datetime, timezone
        invoice.status = PlatformInvoiceStatus.PAID.value
        invoice.paid_at = datetime.now(timezone.utc)
        invoice.payment_method = event.payment_method or "gateway"
        # Reuse the existing paystack_* columns generically — they're typed
        # as nullable strings, the name's historical
        invoice.paystack_reference = event.provider_reference
        logger.info(f"Invoice {invoice.id} marked PAID via {event.payment_method}")
    else:
        # Don't change status on failure — tenant can retry. Just log.
        invoice.failure_reason = event.failure_reason or "Payment failed"
        logger.info(f"Invoice {invoice.id} payment failed: {event.failure_reason}")

    await db.flush()
    return invoice
