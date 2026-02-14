"""WhatsApp webhook API endpoints."""

import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.whatsapp_service import get_whatsapp_service

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Meta webhook verification challenge.

    When setting up the webhook in Meta Developer Console, Meta sends
    a GET request with a challenge that we must echo back.
    """
    whatsapp_service = get_whatsapp_service()

    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("WhatsApp webhook verified successfully")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning(f"WhatsApp webhook verification failed: mode={hub_mode}")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request):
    """
    Process inbound WhatsApp messages.

    Meta sends webhook events for incoming messages, delivery receipts,
    and other events.
    """
    whatsapp_service = get_whatsapp_service()

    # Get raw body for signature verification
    body_bytes = await request.body()

    # Verify HMAC signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    if signature and not whatsapp_service.verify_webhook_signature(body_bytes, signature):
        logger.warning("Invalid WhatsApp webhook signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # Parse the webhook payload
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse WhatsApp webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Extract messages from the webhook
    messages = whatsapp_service.parse_webhook_message(body)

    if messages:
        logger.info(f"Received {len(messages)} WhatsApp message(s)")

        # Process each message
        for msg in messages:
            await process_inbound_message(msg)

    # Always return 200 OK to acknowledge receipt
    # Meta will retry if we don't acknowledge
    return {"status": "ok"}


async def process_inbound_message(msg: dict):
    """
    Process a single inbound WhatsApp message.

    This looks up the sender by phone number and creates
    appropriate message records or notifications.
    """
    from_phone = msg.get("from_phone")
    text = msg.get("text", "")
    message_type = msg.get("message_type")

    logger.info(f"Processing WhatsApp message from {from_phone}: {text[:50]}...")

    # TODO: Implement full message processing:
    # 1. Look up user by whatsapp_phone in users table
    # 2. If found, create a Message record
    # 3. Notify relevant teachers via WebSocket
    # 4. If not found, send auto-reply about registration

    # For now, just log the message
    # This will be fully implemented when we have the parent-student
    # relationship lookup working

    whatsapp_service = get_whatsapp_service()

    # If we can't identify the sender, send a helpful reply
    # (Only if we're in the 24-hour conversation window)
    if not from_phone:
        return

    # Example auto-reply for unregistered numbers:
    # await whatsapp_service.send_text_message(
    #     to_phone=from_phone,
    #     body="Thank you for your message. This number is not registered with ClassUp. "
    #          "Please contact your school to get registered."
    # )
