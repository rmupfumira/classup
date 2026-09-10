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

    @property
    def is_test_mode(self) -> bool:
        """Default: not in test mode. Providers that expose a test_mode
        credential (like PayNow) override this to reflect it. Consumers
        use this to render "no real money" banners to end users."""
        return False

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
# PayNow — Zimbabwe gateway (EcoCash, OneMoney, ZIPIT, Visa/Mastercard)
# ============================================================================

class PayNowProvider(PaymentProvider):
    """Zimbabwe's Paynow gateway. Handles ClassUp SaaS subscription
    payments for Zim tenants — parents-paying-school-fees is a separate
    feature (see BillingInvoice flow).

    Docs: https://developers.paynow.co.zw

    Contract:
      - Initiate: POST form-encoded body to /interface/initiatetransaction.
        Response is form-encoded "Status=Ok&BrowserUrl=…&PollUrl=…&Hash=…"
        (or "Status=Error&Error=…" on failure). We MUST verify the
        response hash before redirecting.
      - Redirect user to BrowserUrl.
      - Paynow POSTs to our resulturl when the transaction status changes,
        form-encoded: reference, paynowreference, amount, status, pollurl,
        hash. We validate the hash, then map ``status`` to PaymentEvent.
      - Statuses treated as succeeded: "Paid" (funds settled), "Awaiting
        Delivery" (paid but held pending our confirm — for services like
        SaaS that's effectively paid), "Delivered" (belt-and-braces).

    Hash algorithm: concatenate all field VALUES in the order they appear
    in the message (skip ``hash``), append the integration key, SHA512,
    uppercase hex. For inbound messages we URL-decode values first.
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
        {"key": "test_mode", "label": "Test mode", "type": "checkbox",
         "help": "Tick this when your Paynow Integration ID is set to Test on Paynow's dashboard. "
                 "Shows a clear warning banner to tenants + skips test-mode-incompatible fields "
                 "(no real money is charged in test mode either way — this just makes it visible)."},
    ]

    INITIATE_URL = "https://www.paynow.co.zw/interface/initiatetransaction"
    # Statuses that mean "the customer's money is committed" — safe to
    # mark the invoice PAID. Everything else is either pending or failed.
    SUCCESS_STATUSES = frozenset({"paid", "awaiting delivery", "delivered"})

    @property
    def integration_id(self) -> str:
        return str(self.credentials.get("integration_id", "")).strip()

    @property
    def integration_key(self) -> str:
        return str(self.credentials.get("integration_key", "")).strip()

    # ────────────────────── Hash helpers ──────────────────────

    def _hash_fields(self, fields: dict[str, str]) -> str:
        """SHA512 hex-upper of concatenated values (in dict-order, skipping
        the ``hash`` key) plus the integration key. Preserving insertion
        order matters — PayNow's PHP reference implementation iterates the
        associative array in add-order.

        Test vector from the docs (integration key
        3e9fed89-60e1-4ce5-ab6e-6b1eb2d4f977) is exercised in
        tests/test_services/test_paynow_provider.py.
        """
        concat = ""
        for key, value in fields.items():
            if key.lower() == "hash":
                continue
            concat += "" if value is None else str(value)
        concat += self.integration_key
        return hashlib.sha512(concat.encode("utf-8")).hexdigest().upper()

    def _verify_hash(self, fields: dict[str, str], provided_hash: str) -> bool:
        """constant-time compare against a computed hash. False if either
        side is missing so a caller can't accidentally accept "no hash"."""
        if not provided_hash:
            return False
        return hmac.compare_digest(
            self._hash_fields(fields).upper(), provided_hash.strip().upper(),
        )

    @staticmethod
    def _parse_form_body(body: bytes | str) -> dict[str, str]:
        """URL-decoded, order-preserving parse of a form body. Values are
        the RAW strings (spaces already un-plussed, %-decoded) which is
        exactly what the hash algorithm expects."""
        from urllib.parse import parse_qsl

        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
        # keep_blank_values=True so a "reference=&…" field doesn't silently
        # disappear before we verify the hash.
        return dict(parse_qsl(text, keep_blank_values=True))

    # ────────────────────── Initiate transaction ──────────────────────

    async def create_checkout(
        self,
        invoice: PlatformInvoice,
        *,
        return_url: str,
        cancel_url: str,
    ) -> CheckoutResult:
        """Create a Paynow transaction and return the URL to redirect the
        user to.

        Paynow doesn't have a distinct cancel flow — the customer clicks
        back or times out, and the transaction stays in "Sent" status.
        We ignore ``cancel_url`` (kept in the signature so the abstraction
        matches Yoco's).
        """
        if not (self.integration_id and self.integration_key):
            raise RuntimeError("Paynow integration_id / integration_key not configured.")

        # ``reference`` must be unique per merchant per transaction. Using
        # the invoice id keeps it stable across retries — if Paynow rejects
        # a duplicate, we know the invoice is already known to them and
        # can look it up rather than creating a new one.
        reference = str(invoice.id)
        amount = f"{Decimal(invoice.amount):.2f}"
        additional_info = (
            f"ClassUp subscription — {invoice.billing_period_start:%b %Y}"
            if getattr(invoice, "billing_period_start", None) else "ClassUp subscription"
        )[:250]  # Paynow silently truncates; we clip explicitly to avoid surprises.

        # Field order matters for the hash. This dict is the order Paynow
        # sees them, and the hash concatenation walks the same order.
        #
        # `authemail` intentionally OMITTED here — it's optional for
        # standard hosted-checkout, and PayNow enforces a hard rule in
        # test mode that authemail must equal the merchant's registered
        # email address. Passing the tenant's own email (which we did
        # briefly) trips that rule and every test-mode initiate errors
        # with "The integration ID is in test mode…". Once we wire up
        # Express Checkout (mobile EcoCash flow) authemail becomes
        # required and we'll add it back, gated on a `merchant_email`
        # credential the admin verifies against PayNow's own record.
        fields: dict[str, str] = {
            "id": self.integration_id,
            "reference": reference,
            "amount": amount,
            "additionalinfo": additional_info,
            "returnurl": return_url,
            "resulturl": self._resulturl_for(return_url),
            "status": "Message",
        }
        fields["hash"] = self._hash_fields(fields)

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                self.INITIATE_URL,
                data=fields,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code >= 300:
            logger.warning(
                "Paynow initiate failed: %s %s", resp.status_code, resp.text[:200]
            )
            raise RuntimeError(f"Paynow rejected the request: HTTP {resp.status_code}")

        parsed = self._parse_form_body(resp.text)
        status = (parsed.get("Status") or parsed.get("status") or "").strip()
        if status.lower() == "error":
            err = parsed.get("Error") or parsed.get("error") or "unknown error"
            logger.warning("Paynow returned Error: %s", err)
            raise RuntimeError(f"Paynow error: {err}")
        if status.lower() != "ok":
            raise RuntimeError(f"Paynow returned unexpected status: {status!r}")

        # Verify the response hash BEFORE trusting BrowserUrl — otherwise
        # a MITM could inject a phishing redirect.
        response_fields = dict(parsed)  # preserve order for hash re-compute
        provided_hash = response_fields.pop("Hash", None) or response_fields.pop("hash", None)
        if not self._verify_hash(response_fields, provided_hash or ""):
            logger.warning("Paynow initiate response hash mismatch — refusing redirect")
            raise RuntimeError("Paynow response hash invalid; refusing to redirect user.")

        browser_url = parsed.get("BrowserUrl") or parsed.get("browserurl")
        poll_url = parsed.get("PollUrl") or parsed.get("pollurl") or ""
        if not browser_url:
            raise RuntimeError("Paynow response missing BrowserUrl.")

        # Store poll_url as the reference — it's the canonical way to
        # reconcile later if the webhook doesn't fire (network issue,
        # missed callback). apply_payment_event stores this on
        # invoice.paystack_reference (name is historical).
        return CheckoutResult(redirect_url=browser_url, reference=poll_url or reference)

    @staticmethod
    def _resulturl_for(return_url: str) -> str:
        """Derive the resulturl from the caller-supplied return_url. Both
        webhook + return_url live under the same host + scheme, but the
        webhook path is fixed at /api/v1/paynow/webhook.

        We accept a return_url that might carry a query string (e.g.
        ?invoice_id=…) and preserve the origin only — no path.
        """
        from urllib.parse import urlparse, urlunparse

        parts = urlparse(return_url)
        if not parts.scheme or not parts.netloc:
            # Called from a test that gave a relative URL — just append
            # the webhook path and hope the caller ran in a context
            # where relative URLs resolve.
            return "/api/v1/paynow/webhook"
        return urlunparse((parts.scheme, parts.netloc, "/api/v1/paynow/webhook", "", "", ""))

    @property
    def is_test_mode(self) -> bool:
        """True when the admin has flagged this integration as test mode
        in /admin/payment-gateways. Drives UI banners so users see a
        clear "no real money" warning before they click Pay."""
        raw = self.credentials.get("test_mode", False)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)

    # ────────────────────── Webhook (result URL) ──────────────────────

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """PayNow authenticates the callback via a hash on the body — no
        signature headers. So headers aren't consulted; ``body`` is the
        form-encoded POST that includes ``hash=…`` alongside the fields.
        """
        if not self.integration_key:
            logger.warning("Paynow integration_key not configured — refusing webhook")
            return False

        fields = self._parse_form_body(body)
        # Pop the hash so re-compute uses the same input the sender did
        provided_hash = fields.pop("hash", None) or fields.pop("Hash", None)
        if not provided_hash:
            logger.warning("Paynow webhook missing hash field")
            return False
        return self._verify_hash(fields, provided_hash)

    def parse_webhook_event(self, body: bytes) -> PaymentEvent:
        """Map a validated (already-verified) result URL POST to our
        provider-agnostic PaymentEvent. ``invoice_id`` comes from the
        ``reference`` field, which we set to str(invoice.id) at initiate
        time — so parsing it back to UUID is the round-trip."""
        fields = self._parse_form_body(body)
        # verify_webhook already checked the hash; parsing is fine to do
        # with the possibly-still-hash-bearing dict.
        raw_status = (fields.get("status") or fields.get("Status") or "").strip()
        succeeded = raw_status.lower() in self.SUCCESS_STATUSES

        # Turn our reference back into a UUID. If it's not a UUID (test
        # data, corrupt POST), leave invoice_id None — apply_payment_event
        # logs + no-ops on that path.
        reference = (fields.get("reference") or fields.get("Reference") or "").strip()
        invoice_id: UUID | None = None
        try:
            if reference:
                invoice_id = UUID(reference)
        except ValueError:
            invoice_id = None

        paynow_ref = (
            fields.get("paynowreference")
            or fields.get("PaynowReference")
            or fields.get("PaynowReference".lower())
        )

        channel = (
            fields.get("paymentchannel")
            or fields.get("PaymentChannel")
            or "paynow"
        )

        return PaymentEvent(
            invoice_id=invoice_id,
            succeeded=succeeded,
            provider_reference=(paynow_ref or None),
            payment_method=f"paynow_{channel.lower().replace(' ', '_')}",
            failure_reason=None if succeeded else raw_status or "Unknown",
            raw=dict(fields),
        )

    async def test_credentials(self) -> tuple[bool, str]:
        """Sanity-check the integration id + key by attempting an
        initiate with a $0.01 amount and a throwaway reference. A real
        transaction is created but never redirected/completed — Paynow
        marks it Cancelled after a short time.

        We keep this lightweight because Paynow has no dedicated ping
        endpoint. If either credential is wrong the server returns an
        Error message before creating anything.
        """
        if not (self.integration_id and self.integration_key):
            return False, "Missing integration ID or key."

        import uuid as _uuid

        test_ref = f"credtest-{_uuid.uuid4().hex[:12]}"
        # Omit authemail — see create_checkout for the full explanation.
        # An empty-string authemail is treated as "not present" for the
        # hash but still trips test-mode validation on some PayNow tenants.
        fields: dict[str, str] = {
            "id": self.integration_id,
            "reference": test_ref,
            "amount": "0.01",
            "additionalinfo": "ClassUp credential test — safe to ignore",
            "returnurl": "https://classup.co.za/paynow/return",
            "resulturl": "https://classup.co.za/api/v1/paynow/webhook",
            "status": "Message",
        }
        fields["hash"] = self._hash_fields(fields)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.INITIATE_URL,
                    data=fields,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as e:
            return False, f"Could not reach Paynow: {e}"
        if resp.status_code >= 500:
            return False, f"Paynow server error: HTTP {resp.status_code}"

        parsed = self._parse_form_body(resp.text)
        status = (parsed.get("Status") or parsed.get("status") or "").strip().lower()
        if status == "ok":
            return True, "Credentials look valid. Test transaction created and will auto-cancel."
        if status == "error":
            err = parsed.get("Error") or parsed.get("error") or "unknown error"
            # "Hash from Website does not match" → integration_key wrong
            # "Invalid id" → integration_id wrong
            return False, f"Paynow rejected credentials: {err}"
        return False, f"Unexpected Paynow response: {resp.text[:200]}"


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

        # Activate the tenant's subscription. Without this the invoice
        # is paid but the tenant stays TRIALING — they paid but got no
        # active plan.
        await _activate_subscription_from_invoice(db, invoice)
    else:
        # Don't change status on failure — tenant can retry. Just log.
        invoice.failure_reason = event.failure_reason or "Payment failed"
        logger.info(f"Invoice {invoice.id} payment failed: {event.failure_reason}")

    await db.flush()
    return invoice


async def _activate_subscription_from_invoice(
    db: AsyncSession, invoice: PlatformInvoice,
) -> None:
    """Flip the subscription linked to a just-paid invoice into ACTIVE
    and extend its current period to match the invoice's coverage.

    Also syncs the plan's feature flags into the tenant so any newly-
    entitled features (WhatsApp, AI bot, etc.) light up immediately.

    Best-effort — a failure here logs but doesn't roll back the PAID
    status. If subscription state drifts, super admin can fix by hand;
    losing the payment record is far worse.
    """
    from app.models.subscription import TenantSubscription, SubscriptionStatus

    try:
        sub = await db.get(TenantSubscription, invoice.subscription_id)
        if sub is None:
            logger.warning(
                "Paid invoice %s has no subscription %s to activate",
                invoice.id, invoice.subscription_id,
            )
            return

        sub.status = SubscriptionStatus.ACTIVE.value
        sub.current_period_start = invoice.billing_period_start
        sub.current_period_end = invoice.billing_period_end
        sub.failed_payment_count = 0
        sub.grace_period_end = None

        # Sync plan features into tenant.settings so anything the tenant
        # was gated out of during trial (per plan) is now live.
        if sub.plan_id:
            from app.services.subscription_service import get_subscription_service
            from app.models.subscription import SubscriptionPlan

            plan = await db.get(SubscriptionPlan, sub.plan_id)
            if plan is not None:
                await get_subscription_service().sync_tenant_features(
                    db, sub.tenant_id, plan,
                )
        logger.info(
            "Subscription %s activated (period %s → %s)",
            sub.id, sub.current_period_start, sub.current_period_end,
        )
    except Exception:
        # Never fail the webhook handler over subscription bookkeeping —
        # the money's already been recorded. Log for follow-up.
        logger.exception(
            "Failed to activate subscription for invoice %s", invoice.id,
        )
