"""Paystack payment gateway integration service."""

import hashlib
import hmac
import logging
from decimal import Decimal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class PaystackService:
    """Handles all Paystack API interactions."""

    def __init__(self):
        self.base_url = settings.paystack_base_url
        self.secret_key = settings.paystack_secret_key

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    @property
    def is_configured(self) -> bool:
        return bool(self.secret_key)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an authenticated request to Paystack API."""
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()

    # ── Customers ──────────────────────────────────────────────

    async def create_customer(
        self, email: str, first_name: str, last_name: str, phone: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Create a Paystack customer."""
        payload = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        }
        if phone:
            payload["phone"] = phone
        if metadata:
            payload["metadata"] = metadata
        data = await self._request("POST", "/customer", json=payload)
        return data["data"]

    async def get_customer(self, customer_code: str) -> dict:
        """Fetch a customer by code."""
        data = await self._request("GET", f"/customer/{customer_code}")
        return data["data"]

    # ── Plans ──────────────────────────────────────────────────

    async def create_plan(
        self, name: str, amount_cents: int, interval: str = "monthly",
        description: str | None = None, currency: str = "ZAR",
    ) -> dict:
        """Create a subscription plan on Paystack.

        Args:
            amount_cents: Amount in kobo/cents (e.g. R299 = 29900)
            interval: hourly, daily, weekly, monthly, quarterly, biannually, annually
        """
        payload = {
            "name": name,
            "amount": amount_cents,
            "interval": interval,
            "currency": currency,
        }
        if description:
            payload["description"] = description
        data = await self._request("POST", "/plan", json=payload)
        return data["data"]

    async def list_plans(self) -> list[dict]:
        """List all plans."""
        data = await self._request("GET", "/plan")
        return data["data"]

    async def get_plan(self, plan_code: str) -> dict:
        """Get a specific plan."""
        data = await self._request("GET", f"/plan/{plan_code}")
        return data["data"]

    async def update_plan(self, plan_code: str, name: str | None = None,
                          amount_cents: int | None = None) -> dict:
        """Update a plan."""
        payload = {}
        if name:
            payload["name"] = name
        if amount_cents is not None:
            payload["amount"] = amount_cents
        data = await self._request("PUT", f"/plan/{plan_code}", json=payload)
        return data["data"]

    # ── Subscriptions ──────────────────────────────────────────

    async def create_subscription(
        self, customer_email_or_code: str, plan_code: str,
        authorization_code: str | None = None,
        start_date: str | None = None,
    ) -> dict:
        """Create a recurring subscription.

        If authorization_code is provided, charges start immediately.
        Otherwise Paystack sends an authorization email.
        """
        payload = {
            "customer": customer_email_or_code,
            "plan": plan_code,
        }
        if authorization_code:
            payload["authorization"] = authorization_code
        if start_date:
            payload["start_date"] = start_date
        data = await self._request("POST", "/subscription", json=payload)
        return data["data"]

    async def get_subscription(self, subscription_code: str) -> dict:
        """Get subscription details."""
        data = await self._request("GET", f"/subscription/{subscription_code}")
        return data["data"]

    async def enable_subscription(self, subscription_code: str, email_token: str) -> dict:
        """Enable a disabled subscription."""
        payload = {"code": subscription_code, "token": email_token}
        data = await self._request("POST", "/subscription/enable", json=payload)
        return data

    async def disable_subscription(self, subscription_code: str, email_token: str) -> dict:
        """Disable (pause) a subscription."""
        payload = {"code": subscription_code, "token": email_token}
        data = await self._request("POST", "/subscription/disable", json=payload)
        return data

    # ── Transactions (one-off or initialize) ───────────────────

    async def initialize_transaction(
        self, email: str, amount_cents: int, reference: str | None = None,
        callback_url: str | None = None, metadata: dict | None = None,
        plan_code: str | None = None, currency: str = "ZAR",
    ) -> dict:
        """Initialize a transaction (returns authorization_url for redirect).

        Used for first-time card capture or one-off payments.
        """
        payload = {
            "email": email,
            "amount": amount_cents,
            "currency": currency,
        }
        if reference:
            payload["reference"] = reference
        if callback_url:
            payload["callback_url"] = callback_url
        if metadata:
            payload["metadata"] = metadata
        if plan_code:
            payload["plan"] = plan_code
        data = await self._request("POST", "/transaction/initialize", json=payload)
        return data["data"]

    async def verify_transaction(self, reference: str) -> dict:
        """Verify a transaction by reference."""
        data = await self._request("GET", f"/transaction/verify/{reference}")
        return data["data"]

    async def charge_authorization(
        self, authorization_code: str, email: str, amount_cents: int,
        reference: str | None = None, currency: str = "ZAR",
    ) -> dict:
        """Charge a previously authorized card."""
        payload = {
            "authorization_code": authorization_code,
            "email": email,
            "amount": amount_cents,
            "currency": currency,
        }
        if reference:
            payload["reference"] = reference
        data = await self._request("POST", "/transaction/charge_authorization", json=payload)
        return data["data"]

    # ── Webhook verification ───────────────────────────────────

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify Paystack webhook HMAC signature."""
        secret = settings.paystack_webhook_secret or self.secret_key
        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def rands_to_cents(amount: Decimal) -> int:
        """Convert Rands to cents for Paystack API."""
        return int(amount * 100)

    @staticmethod
    def cents_to_rands(cents: int) -> Decimal:
        """Convert cents from Paystack to Rands."""
        return Decimal(cents) / Decimal(100)


def get_paystack_service() -> PaystackService:
    """Get Paystack service instance."""
    return PaystackService()
