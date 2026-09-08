"""WhatsApp webhook API endpoints.

POC flow — every inbound message:
  1. Verified via HMAC (using the app_secret configured in
     /admin/whatsapp-settings, falling back to APP_SECRET_KEY env var).
  2. Deduplicated on Meta's message id (UNIQUE index).
  3. Persisted to whatsapp_inbound_messages so the admin page can show it.
  4. Sender matched against users.whatsapp_phone (across all tenants).
  5. Auto-replied with a "we got it" text so the sender knows the pipe works.

No bot flow yet — that lands in later phases. This file's job is purely
to prove the plumbing is live.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_db_context
from app.models import User, WhatsAppInboundMessage
from app.services.whatsapp_service import (
    get_config,
    get_whatsapp_service_from_db,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    db: AsyncSession = Depends(get_db),
):
    """Meta webhook verification handshake.

    When the super admin registers our webhook URL in the Meta Developer
    Console, Meta hits this endpoint with a `hub.verify_token` value that
    must match what's saved in /admin/whatsapp-settings. If it does, we
    echo the `hub.challenge` back verbatim.
    """
    cfg = await get_config(db)
    if hub_mode == "subscribe" and hub_verify_token == cfg.verify_token and cfg.verify_token:
        logger.info("WhatsApp webhook verified successfully")
        return Response(content=hub_challenge or "", media_type="text/plain")

    logger.warning(
        f"WhatsApp webhook verification failed: mode={hub_mode}, "
        f"token_matches={hub_verify_token == cfg.verify_token}, "
        f"token_configured={bool(cfg.verify_token)}"
    )
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive inbound events from Meta (messages + delivery receipts).

    We always return 200 to keep Meta happy — if we return anything else,
    Meta retries + eventually disables the webhook. Errors get logged.
    """
    service = await get_whatsapp_service_from_db(db)

    # Get raw body for signature verification
    body_bytes = await request.body()

    # Verify HMAC signature (Meta sets X-Hub-Signature-256)
    signature = request.headers.get("X-Hub-Signature-256", "")
    if signature and not service.verify_webhook_signature(body_bytes, signature):
        logger.warning("Invalid WhatsApp webhook signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # Parse the webhook payload
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse WhatsApp webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Meta also fires webhooks for delivery / read receipts — those don't
    # have "messages" set, only "statuses". parse_webhook_message returns []
    # for those, which is exactly what we want (no-op).
    messages = service.parse_webhook_message(body)

    if messages:
        logger.info(f"Received {len(messages)} WhatsApp message(s)")
        for msg in messages:
            try:
                await process_inbound_message(msg, body)
            except Exception:
                # Swallow per-message errors so one bad message never
                # blocks the batch — Meta would keep retrying it forever.
                logger.exception(
                    "process_inbound_message failed for msg %s",
                    msg.get("message_id"),
                )

    return {"status": "ok"}


async def process_inbound_message(msg: dict, raw_body: dict) -> None:
    """Store the message, identify the sender, send an auto-reply.

    POC behaviour — every inbound message triggers a canned reply so the
    admin can prove the pipeline works. Later phases replace the auto-reply
    with a real bot flow (menu, intents, state machine).
    """
    from_phone = msg.get("from_phone")
    text = msg.get("text") or ""
    message_type = msg.get("message_type") or "unknown"
    meta_message_id = msg.get("message_id")

    if not from_phone:
        logger.warning("Inbound WhatsApp message with no from_phone; skipping")
        return

    # Use a dedicated write session so a failure here doesn't leak into
    # the request's main session (the webhook handler doesn't need to
    # commit the message row atomically with anything else).
    async with get_db_context() as db:
        # Dedup via UNIQUE index on meta_message_id — Meta retries webhooks
        # on 5xx, and once in a while we get the same message twice.
        if meta_message_id:
            existing = await db.execute(
                select(WhatsAppInboundMessage).where(
                    WhatsAppInboundMessage.meta_message_id == meta_message_id
                )
            )
            if existing.scalar_one_or_none():
                logger.info(
                    f"Skipping duplicate WhatsApp message {meta_message_id} "
                    f"from {from_phone}"
                )
                return

        # Look up sender by phone. WhatsApp gives us the number without a
        # leading +, but users.whatsapp_phone stores E.164 with the +. Try
        # both spellings.
        matched_user = await _find_user_by_phone(db, from_phone)

        record = WhatsAppInboundMessage(
            tenant_id=matched_user.tenant_id if matched_user else None,
            matched_user_id=matched_user.id if matched_user else None,
            from_phone=from_phone,
            message_type=message_type,
            text=text[:8000] if text else None,
            meta_message_id=meta_message_id,
            raw_payload=raw_body,
        )
        db.add(record)
        await db.flush()

        # Fire an auto-reply so the sender knows the pipeline received their
        # message. Uses free-form text — safe within the 24hr WhatsApp
        # session window (which is always open right after a user sends
        # to us). Best-effort: any failure is logged on the record but
        # doesn't crash the webhook.
        try:
            svc = await get_whatsapp_service_from_db(db)
            if matched_user:
                greeting = f"Hi {matched_user.first_name}, ClassUp received your message. "
            else:
                greeting = "Hi there! ClassUp received your message. "

            reply = (
                greeting
                + "Full WhatsApp features are on the way — for now, log in at "
                + "https://classup.co.za to check attendance, balances, and reports."
            )
            await svc.send_text_message(to_phone=from_phone, body=reply)
            record.auto_replied = True
        except Exception as e:
            logger.exception(
                "Failed to send auto-reply to %s for message %s",
                from_phone, meta_message_id,
            )
            record.auto_reply_error = str(e)[:500]

        await db.commit()


async def _find_user_by_phone(
    db: AsyncSession, from_phone: str
) -> User | None:
    """Look up a user by their WhatsApp phone across all tenants.

    Meta strips the leading +; the DB usually stores it with the + (E.164).
    We try both and prefer an active, non-deleted, opted-in user. If the
    number matches an account that hasn't opted in yet we still return it
    — the caller decides whether to auto-reply anyway.
    """
    candidates = {from_phone, f"+{from_phone.lstrip('+')}"}
    result = await db.execute(
        select(User)
        .where(
            User.whatsapp_phone.in_(list(candidates)),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()
