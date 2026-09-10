"""Public webhook endpoints — payment providers POST here to notify us
of payment outcomes.

These routes are PUBLIC (no JWT auth) because the caller is the gateway,
not a logged-in user. They MUST be added to AuthMiddleware's EXEMPT_PATHS.

Each provider has its own route since the verification + parsing differs.
The body is provider-specific JSON; we hand it to the provider class to
parse, then call ``apply_payment_event`` to mark the invoice paid.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import gateway_service
from app.services.gateway_service import PROVIDER_REGISTRY

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Payment Webhooks"])


async def _handle_webhook(provider_id: str, request: Request, db: AsyncSession):
    """Shared handler: load active config, verify signature, parse, apply."""
    cfg = await gateway_service.get_config(db)
    if not cfg.configured or cfg.provider_id != provider_id:
        # The gateway is firing webhooks but we no longer have it configured
        # (admin disabled / switched providers). Log + 200 OK so the gateway
        # stops retrying — there's no legitimate retry value here.
        logger.warning(
            f"Received {provider_id} webhook but configured provider is "
            f"'{cfg.provider_id}' (enabled={cfg.is_enabled}). Ignoring."
        )
        return {"status": "ignored"}

    cls = PROVIDER_REGISTRY.get(provider_id)
    if not cls:
        raise HTTPException(status_code=404, detail="Unknown provider")
    provider = cls(cfg.credentials)

    body = await request.body()
    # Normalise headers to a plain dict — case-insensitive lookup happens
    # inside each provider's verify
    headers = {k.lower(): v for k, v in request.headers.items()}

    if not provider.verify_webhook(headers, body):
        logger.warning(f"{provider_id} webhook signature verification FAILED")
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = provider.parse_webhook_event(body)
    invoice = await gateway_service.apply_payment_event(db, event)
    await db.commit()

    return {
        "status": "ok",
        "succeeded": event.succeeded,
        "invoice_marked_paid": (invoice is not None and event.succeeded),
    }


@router.post("/yoco/webhook")
async def yoco_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Yoco posts here on payment.succeeded / payment.failed events.

    Public endpoint (no JWT). Signature is verified via the webhook secret
    configured in /admin/payment-gateways. Anti-replay window is 5 minutes.
    """
    return await _handle_webhook("yoco", request, db)


@router.post("/paynow/webhook")
async def paynow_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Paynow (Zim) result URL endpoint.

    Body is application/x-www-form-urlencoded (NOT JSON) — Paynow POSTs
    the transaction status change as form fields including a SHA512
    hash we verify against the integration key configured in
    /admin/payment-gateways.

    Paynow retries up to 10 times on non-2xx responses; we always
    return 200 (with a status body) so retries stop, and log unusual
    payloads for later review.
    """
    return await _handle_webhook("paynow", request, db)
