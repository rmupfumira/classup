"""Tests for the PayNow provider — Zim's payment gateway used for
platform subscription billing.

The docs give us an exact hash test vector (integration key +
sample fields → expected SHA512 hex uppercase). If our hash function
doesn't reproduce it, every PayNow call will fail with "Hash from
Website does not match" and no transactions will initiate.

Also covers:
  - create_checkout success path (mocked httpx response)
  - create_checkout hash-mismatch on response (MITM protection —
    we MUST refuse to redirect if PayNow's response hash is invalid)
  - create_checkout Error response surfaces the error message
  - webhook verify_webhook returns False on missing / wrong hash
  - webhook parse_webhook_event maps every documented status
  - resulturl derivation from arbitrary return_url
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest

from app.services.gateway_service import PayNowProvider


# Canonical test vector from https://developers.paynow.co.zw docs
# (Generating Hash page — worked example)
DOC_INTEGRATION_KEY = "3e9fed89-60e1-4ce5-ab6e-6b1eb2d4f977"
DOC_EXPECTED_HASH = (
    "2A033FC38798D913D42ECB786B9B19645ADEDBDE788862032F1BD82CF3B92DEF"
    "84F316385D5B40DBB35F1A4FD7D5BFE73835174136463CDD48C9366B0749C689"
)


def _provider(**cred_overrides) -> PayNowProvider:
    return PayNowProvider(credentials={
        "integration_id": cred_overrides.get("integration_id", "1201"),
        "integration_key": cred_overrides.get("integration_key", DOC_INTEGRATION_KEY),
    })


def _fake_invoice(*, invoice_id=None, amount="99.99", tenant_email="head@school.zw"):
    """Bare-bones PlatformInvoice stand-in. The provider only reads
    id / amount / tenant.email / billing_period_start."""
    return SimpleNamespace(
        id=invoice_id or UUID("12345678-1234-1234-1234-123456789abc"),
        amount=Decimal(amount),
        billing_period_start=None,
        tenant=SimpleNamespace(email=tenant_email),
    )


class TestHashAlgorithm:
    """The docs' worked example is our contract with PayNow's server.
    Break this and every transaction fails with 'Hash mismatch'."""

    def test_docs_worked_example_hash(self):
        """Reproduce the exact test vector from the Generating Hash
        page. This is the strongest confidence signal we can get
        without hitting PayNow's live servers."""
        provider = _provider()
        fields = {
            "id": "1201",
            "reference": "TEST REF",
            "amount": "99.99",
            "additionalinfo": "A test ticket transaction",
            "returnurl": "http://www.google.com/search?q=returnurl",
            "resulturl": "http://www.google.com/search?q=resulturl",
            "status": "Message",
        }
        assert provider._hash_fields(fields) == DOC_EXPECTED_HASH

    def test_hash_skips_hash_key(self):
        """A caller who leaves 'hash' in the dict when re-computing
        (which happens naturally when verifying an inbound message)
        shouldn't get a different result."""
        provider = _provider()
        fields = {
            "id": "1201",
            "reference": "TEST REF",
            "amount": "99.99",
            "additionalinfo": "A test ticket transaction",
            "returnurl": "http://www.google.com/search?q=returnurl",
            "resulturl": "http://www.google.com/search?q=resulturl",
            "status": "Message",
            "hash": "should_be_ignored",
        }
        assert provider._hash_fields(fields) == DOC_EXPECTED_HASH

    def test_hash_case_insensitive_hash_key(self):
        """PayNow's docs alternate between 'hash' and 'Hash'. The
        skip must match either casing — otherwise verifying a webhook
        whose sender used 'Hash' will double-count it."""
        provider = _provider()
        base = {"id": "1201", "reference": "x"}
        h1 = provider._hash_fields(base)
        h2 = provider._hash_fields({**base, "Hash": "ignored"})
        h3 = provider._hash_fields({**base, "HASH": "ignored"})
        assert h1 == h2 == h3

    def test_verify_hash_constant_time(self):
        """A missing / empty hash must fail cleanly rather than
        matching an accidental empty string."""
        provider = _provider()
        fields = {"id": "1201"}
        assert not provider._verify_hash(fields, "")
        assert not provider._verify_hash(fields, None)

    def test_verify_hash_accepts_lowercase(self):
        """PayNow always sends uppercase hex, but we shouldn't be
        fragile against a well-formed lowercase hash either."""
        provider = _provider()
        fields = {"id": "1201"}
        expected = provider._hash_fields(fields)
        assert provider._verify_hash(fields, expected.lower())


class TestCreateCheckout:
    async def test_successful_initiate_returns_browser_url(self):
        """Happy path: PayNow returns Status=Ok with a valid hash;
        we return the BrowserUrl for redirect."""
        provider = _provider()
        invoice = _fake_invoice()

        # Build a response that would pass verification. Response order
        # (Status, BrowserUrl, PollUrl) is what PayNow's example shows.
        browser_url = "https://www.paynow.co.zw/Payment/ConfirmPayment/1169"
        poll_url = "https://www.paynow.co.zw/Interface/CheckPayment/?guid=abc"
        response_fields = {
            "Status": "Ok",
            "BrowserUrl": browser_url,
            "PollUrl": poll_url,
        }
        response_hash = provider._hash_fields(response_fields)
        response_body = (
            f"Status=Ok&BrowserUrl={browser_url}&PollUrl={poll_url}"
            f"&Hash={response_hash}"
        )

        with patch("httpx.AsyncClient") as mock_client:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_ctx.post = AsyncMock(return_value=SimpleNamespace(
                status_code=200, text=response_body,
            ))
            mock_client.return_value = mock_ctx

            result = await provider.create_checkout(
                invoice, return_url="https://school.example.com/paid",
                cancel_url="https://school.example.com/cancel",
            )
        assert result.redirect_url == browser_url
        # We store the poll URL as reference — used later for reconciliation
        assert result.reference == poll_url

    async def test_hash_mismatch_on_response_refuses_redirect(self):
        """A MITM could rewrite BrowserUrl to a phishing page. We
        catch it by re-hashing the response with the integration key
        and refusing to redirect if it doesn't match."""
        provider = _provider()
        invoice = _fake_invoice()
        # Response has a WRONG hash — real MITM scenario.
        response_body = (
            "Status=Ok&BrowserUrl=https://attacker.example.com/phish"
            "&PollUrl=https://paynow.co.zw/poll&Hash=DEADBEEF"
        )
        with patch("httpx.AsyncClient") as mock_client:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_ctx.post = AsyncMock(return_value=SimpleNamespace(
                status_code=200, text=response_body,
            ))
            mock_client.return_value = mock_ctx

            with pytest.raises(RuntimeError, match="hash invalid"):
                await provider.create_checkout(
                    invoice, return_url="https://school.example.com/paid",
                    cancel_url="https://school.example.com/cancel",
                )

    async def test_error_response_surfaces_message(self):
        """PayNow returns Status=Error&Error=... for anything the
        server rejects. We must surface the message so admins can
        debug (wrong ID? bad amount? etc.)."""
        provider = _provider()
        invoice = _fake_invoice()
        with patch("httpx.AsyncClient") as mock_client:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_ctx.post = AsyncMock(return_value=SimpleNamespace(
                status_code=200,
                text="Status=Error&Error=Invalid+amount+field",
            ))
            mock_client.return_value = mock_ctx

            with pytest.raises(RuntimeError, match="Invalid amount field"):
                await provider.create_checkout(
                    invoice, return_url="https://school.example.com/paid",
                    cancel_url="https://school.example.com/cancel",
                )

    async def test_missing_credentials_raises(self):
        """Sanity: don't send unauthenticated requests."""
        provider = PayNowProvider(credentials={})
        with pytest.raises(RuntimeError, match="not configured"):
            await provider.create_checkout(
                _fake_invoice(), return_url="https://x.com", cancel_url="https://x.com",
            )


class TestResulturlDerivation:
    """resulturl is derived from return_url — kept together so both
    live under the same origin. Test that arbitrary return_urls
    produce a sensible webhook URL."""

    @pytest.mark.parametrize("return_url,expected", [
        ("https://school.classup.co.za/paid",
         "https://school.classup.co.za/api/v1/paynow/webhook"),
        ("https://classup.co.za/billing/12/paid?ref=abc",
         "https://classup.co.za/api/v1/paynow/webhook"),
        # localhost during dev
        ("http://localhost:8000/subscription?paid=1",
         "http://localhost:8000/api/v1/paynow/webhook"),
    ])
    def test_derives_from_return_url(self, return_url, expected):
        assert PayNowProvider._resulturl_for(return_url) == expected


class TestWebhookVerification:
    def test_valid_webhook_passes(self):
        provider = _provider()
        fields = {
            "reference": "12345678-1234-1234-1234-123456789abc",
            "paynowreference": "9876543",
            "amount": "150.00",
            "status": "Paid",
            "pollurl": "https://paynow.co.zw/poll",
        }
        h = provider._hash_fields(fields)
        body = "&".join(f"{k}={v}" for k, v in fields.items()) + f"&hash={h}"
        assert provider.verify_webhook({}, body.encode())

    def test_missing_hash_rejected(self):
        provider = _provider()
        body = "reference=x&amount=1.00&status=Paid"
        assert not provider.verify_webhook({}, body.encode())

    def test_wrong_hash_rejected(self):
        """The whole point of the hash — tampering is caught."""
        provider = _provider()
        body = "reference=x&amount=1.00&status=Paid&hash=DEADBEEF"
        assert not provider.verify_webhook({}, body.encode())

    def test_tampered_amount_rejected(self):
        """A real attack: the attacker replays a valid webhook with a
        modified amount. The hash was computed for the old amount, so
        the re-computed hash won't match."""
        provider = _provider()
        original = {
            "reference": "12345678-1234-1234-1234-123456789abc",
            "amount": "150.00",
            "status": "Paid",
            "pollurl": "https://paynow.co.zw/poll",
        }
        h = provider._hash_fields(original)
        tampered = dict(original, amount="1500.00")  # wanted to pay less
        body = "&".join(f"{k}={v}" for k, v in tampered.items()) + f"&hash={h}"
        assert not provider.verify_webhook({}, body.encode())

    def test_missing_integration_key_rejected(self):
        """Refuse to verify if the credential is missing rather than
        silently accepting (which would happen with a naive empty-key
        hash)."""
        provider = PayNowProvider(credentials={"integration_id": "1201"})
        assert not provider.verify_webhook({}, b"reference=x&hash=anything")


class TestEventParsing:
    @pytest.mark.parametrize("status,succeeded", [
        ("Paid", True),
        ("paid", True),  # case-insensitive
        ("Awaiting Delivery", True),
        ("Delivered", True),
        ("Cancelled", False),
        ("Sent", False),
        ("Created", False),
        ("Disputed", False),
        ("Refunded", False),
    ])
    def test_status_mapping(self, status, succeeded):
        provider = _provider()
        # url-encode the space in "Awaiting Delivery" the way PayNow does
        from urllib.parse import quote_plus
        body = (
            f"reference=12345678-1234-1234-1234-123456789abc"
            f"&paynowreference=9876"
            f"&amount=1.00"
            f"&status={quote_plus(status)}"
            f"&pollurl=https://paynow.co.zw/poll"
            f"&hash=IGNORED_BECAUSE_ALREADY_VERIFIED"
        )
        event = provider.parse_webhook_event(body.encode())
        assert event.succeeded == succeeded
        assert event.invoice_id == UUID("12345678-1234-1234-1234-123456789abc")
        assert event.provider_reference == "9876"
        if not succeeded:
            assert event.failure_reason  # non-empty

    def test_missing_reference_returns_no_invoice_id(self):
        """Malformed webhook — we return None invoice_id so
        apply_payment_event logs + no-ops rather than crashing."""
        provider = _provider()
        body = b"amount=1.00&status=Paid&hash=x"
        event = provider.parse_webhook_event(body)
        assert event.invoice_id is None
        assert event.succeeded is True

    def test_non_uuid_reference_returns_none(self):
        """A non-UUID reference (from a manual test or misconfigured
        integration) must not crash the parser."""
        provider = _provider()
        body = b"reference=not-a-uuid&amount=1.00&status=Paid&hash=x"
        event = provider.parse_webhook_event(body)
        assert event.invoice_id is None

    def test_payment_channel_reflected_in_method(self):
        """When PayNow tells us the channel (EcoCash / Visa / etc.),
        we record it so accounting can differentiate mobile-money
        payments from card payments."""
        provider = _provider()
        body = (
            b"reference=12345678-1234-1234-1234-123456789abc"
            b"&paynowreference=X"
            b"&amount=1.00"
            b"&status=Paid"
            b"&paymentchannel=Ecocash"
            b"&hash=x"
        )
        event = provider.parse_webhook_event(body)
        assert event.payment_method == "paynow_ecocash"


class TestTestModeToggle:
    """The admin can flip PayNow into test mode via the credential
    form. is_test_mode reflects that so UIs can render banners."""

    def test_default_is_not_test_mode(self):
        assert _provider().is_test_mode is False

    @pytest.mark.parametrize("raw", [True, "true", "True", "1", "on", "yes"])
    def test_truthy_values_enable_test_mode(self, raw):
        p = PayNowProvider(credentials={
            "integration_id": "1", "integration_key": "k", "test_mode": raw,
        })
        assert p.is_test_mode is True

    @pytest.mark.parametrize("raw", [False, "false", "0", "off", "no", ""])
    def test_falsy_values_disable_test_mode(self, raw):
        p = PayNowProvider(credentials={
            "integration_id": "1", "integration_key": "k", "test_mode": raw,
        })
        assert p.is_test_mode is False


class TestAuthemailNotSent:
    """PayNow's test-mode rejects requests that include authemail unless
    it exactly matches the merchant's registered email. We dropped the
    field entirely for the standard checkout — this test guards against
    accidentally adding it back."""

    async def test_create_checkout_omits_authemail(self):
        provider = _provider()
        invoice = _fake_invoice()
        # Build a valid Ok response so the flow completes.
        browser_url = "https://www.paynow.co.zw/Payment/x"
        poll_url = "https://www.paynow.co.zw/Interface/x"
        response_fields = {"Status": "Ok", "BrowserUrl": browser_url, "PollUrl": poll_url}
        response_body = (
            f"Status=Ok&BrowserUrl={browser_url}&PollUrl={poll_url}"
            f"&Hash={provider._hash_fields(response_fields)}"
        )

        captured = {}
        with patch("httpx.AsyncClient") as mock_client:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            async def capture_post(url, data=None, headers=None):
                captured["data"] = data
                return SimpleNamespace(status_code=200, text=response_body)
            mock_ctx.post = capture_post
            mock_client.return_value = mock_ctx

            await provider.create_checkout(
                invoice, return_url="https://school.example.com/paid",
                cancel_url="https://school.example.com/cancel",
            )

        assert "authemail" not in captured["data"], (
            "authemail is present in the initiate payload — this trips "
            "PayNow test-mode's 'authemail must match merchant email' rule "
            "and every test-mode transaction errors out."
        )


class TestFormBodyParsing:
    """The parser must preserve order for hash verification, and
    URL-decode values (spaces + %-encoded chars) BEFORE the hash is
    concatenated — matches PayNow's docs 'URL decode any values first'.
    """

    def test_preserves_insertion_order(self):
        provider = _provider()
        parsed = provider._parse_form_body("a=1&b=2&c=3")
        assert list(parsed.keys()) == ["a", "b", "c"]

    def test_decodes_plus_as_space(self):
        provider = _provider()
        parsed = provider._parse_form_body("status=Awaiting+Delivery")
        assert parsed["status"] == "Awaiting Delivery"

    def test_decodes_percent_encoding(self):
        provider = _provider()
        parsed = provider._parse_form_body(
            "url=https%3A%2F%2Fpaynow.co.zw%2Fpoll"
        )
        assert parsed["url"] == "https://paynow.co.zw/poll"

    def test_handles_bytes_and_str(self):
        provider = _provider()
        assert provider._parse_form_body(b"a=1") == provider._parse_form_body("a=1")
