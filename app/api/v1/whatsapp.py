"""WhatsApp webhook API endpoints.

Inbound flow — every message:
  1. Verified via HMAC (using the app_secret configured in
     /admin/whatsapp-settings, falling back to APP_SECRET_KEY env var).
  2. Deduplicated on Meta's message id (UNIQUE index).
  3. Persisted to whatsapp_inbound_messages so the admin page can show it.
  4. Sender matched against users.whatsapp_phone (across all tenants).
  5. Dispatched to the bot: mode resolved from plan + tenant settings
     (OFF / MENU / AI); each mode owns its own reply logic.

The bot handlers themselves live in app.services.whatsapp_bot. This file
just owns the webhook plumbing.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_db_context
from app.models import Tenant, User, WhatsAppInboundMessage
from app.services.whatsapp_bot import BotMode, handle_inbound_message
from app.services.whatsapp_menu_bot import (
    ButtonReply, DocumentReply, ImageReply, ListReply, MultiReply, TextReply,
)
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
    """Store the message, identify the sender, dispatch to the bot.

    The bot module resolves the mode (OFF / MENU / AI) and returns a reply
    (or None for OFF). We record what happened on the message row so the
    admin page can show it.
    """
    from_phone = msg.get("from_phone")
    text = msg.get("text") or ""
    message_type = msg.get("message_type") or "unknown"
    meta_message_id = msg.get("message_id")
    # Parser populates this for button/list replies — carries the state
    # machine's ``menu:<flow>`` / ``pick:<flow>:<child>`` payload.
    interactive_id = msg.get("interactive_id")

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

        # Look up sender by phone. Two passes:
        #   1. Strict match on whatsapp_phone — the user opted in.
        #   2. Fallback: same phone lives in the regular users.phone
        #      field but the parent hasn't set whatsapp_phone yet. That
        #      IS a known user, they just don't know they had to fill
        #      in a separate field. Send them a one-time onboarding
        #      prompt so they can enable it themselves.
        matched_user = await _find_user_by_phone(db, from_phone)
        onboarding_user = None
        if matched_user is None:
            onboarding_user = await _find_user_by_regular_phone(db, from_phone)

        tenant = None
        if matched_user and matched_user.tenant_id:
            tenant = await db.get(Tenant, matched_user.tenant_id)

        record = WhatsAppInboundMessage(
            # Record the match against onboarding_user too — the admin
            # page needs to see who this actually is, not "unknown".
            tenant_id=(matched_user or onboarding_user).tenant_id
                if (matched_user or onboarding_user) else None,
            matched_user_id=(matched_user or onboarding_user).id
                if (matched_user or onboarding_user) else None,
            from_phone=from_phone,
            message_type=message_type,
            text=text[:8000] if text else None,
            meta_message_id=meta_message_id,
            raw_payload=raw_body,
        )
        db.add(record)
        await db.flush()

        # Onboarding branch — short-circuits the bot dispatch. Guarded
        # by a 24h Redis dedupe so we don't spam every message.
        if matched_user is None and onboarding_user is not None:
            try:
                sent = await _send_onboarding_if_new(
                    from_phone=from_phone,
                    user=onboarding_user,
                    db=db,
                )
                if sent:
                    record.auto_replied = True
            except Exception as e:
                logger.exception(
                    "Onboarding prompt failed for %s (message %s)",
                    from_phone, meta_message_id,
                )
                record.auto_reply_error = str(e)[:500]
            await db.commit()
            return

        # Bot dispatch: resolve mode, get a reply (or None for OFF), send it.
        # Best-effort — any failure is logged on the record but never crashes
        # the webhook (Meta would keep retrying forever).
        try:
            mode, reply = await handle_inbound_message(
                db=db,
                tenant=tenant,
                user=matched_user,
                from_phone=from_phone,
                text=text,
                interactive_id=interactive_id,
            )
            if reply is not None and mode is not BotMode.OFF:
                svc = await get_whatsapp_service_from_db(db)
                await _send_reply(
                    svc, from_phone, reply,
                    db=db,
                    tenant_id=(tenant.id if tenant else None),
                    target_user_id=(matched_user.id if matched_user else None),
                    inbound_message_id=record.id,
                )
                record.auto_replied = True
        except Exception as e:
            logger.exception(
                "Bot dispatch failed for %s (message %s)",
                from_phone, meta_message_id,
            )
            record.auto_reply_error = str(e)[:500]

        await db.commit()


async def _send_reply(
    svc,
    from_phone: str,
    reply,
    *,
    db=None,
    tenant_id=None,
    target_user_id=None,
    inbound_message_id=None,
) -> None:
    """Dispatch one bot reply through the right WhatsApp API method.

    Split out so both the main path and future async workers can share
    the send logic. MultiReply recurses so a caller can chain multiple
    parts (e.g. "here's Sarah's balance" text + the invoice PDF)
    without knowing the wire-level details.

    When ``db`` is provided, each part is also persisted to
    ``whatsapp_outbound_messages`` linked back to the inbound that
    triggered it, so super admin can reconstruct the conversation.
    Best-effort — persistence failures never bubble.
    """
    from app.services.whatsapp_log import record_outbound

    async def _log(message_type: str, body_text: str | None, response, error=None):
        if db is None:
            return
        await record_outbound(
            db,
            to_phone=from_phone,
            message_type=message_type,
            body_text=body_text,
            tenant_id=tenant_id,
            target_user_id=target_user_id,
            inbound_message_id=inbound_message_id,
            response=response,
            error=error,
        )

    if isinstance(reply, TextReply):
        resp = await svc.send_text_message(to_phone=from_phone, body=reply.body)
        await _log("text", reply.body, resp)
    elif isinstance(reply, ButtonReply):
        resp = await svc.send_interactive_buttons(
            to_phone=from_phone,
            body=reply.body,
            buttons=reply.buttons,
            header=reply.header,
            footer=reply.footer,
        )
        btn_titles = ", ".join(b.get("title", "") for b in reply.buttons)
        await _log(
            "interactive_buttons",
            f"{reply.body}\n[buttons: {btn_titles}]",
            resp,
        )
    elif isinstance(reply, ListReply):
        resp = await svc.send_interactive_list(
            to_phone=from_phone,
            body=reply.body,
            button_text=reply.button_text,
            sections=reply.sections,
            header=reply.header,
            footer=reply.footer,
        )
        row_titles = [
            r.get("title", "")
            for sec in reply.sections
            for r in sec.get("rows", [])
        ]
        await _log(
            "interactive_list",
            f"{reply.body}\n[list: {', '.join(row_titles)}]",
            resp,
        )
    elif isinstance(reply, DocumentReply):
        resp = await svc.send_document_from_bytes(
            to_phone=from_phone,
            file_bytes=reply.file_bytes,
            mime_type=reply.mime_type,
            filename=reply.filename,
            caption=reply.caption,
        )
        await _log(
            "document",
            f"[document: {reply.filename}]{(' — ' + reply.caption) if reply.caption else ''}",
            resp,
        )
    elif isinstance(reply, ImageReply):
        resp = await svc.send_image_from_bytes(
            to_phone=from_phone,
            image_bytes=reply.image_bytes,
            mime_type=reply.mime_type,
            caption=reply.caption,
        )
        await _log(
            "image",
            f"[image]{(' — ' + reply.caption) if reply.caption else ''}",
            resp,
        )
    elif isinstance(reply, MultiReply):
        # Send each part in sequence — order matters (text intro → doc).
        # If one part fails the others still fire, matching best-effort
        # semantics everywhere else.
        for part in reply.parts:
            try:
                await _send_reply(
                    svc, from_phone, part,
                    db=db, tenant_id=tenant_id,
                    target_user_id=target_user_id,
                    inbound_message_id=inbound_message_id,
                )
            except Exception:
                logger.exception(
                    "MultiReply part failed to send to %s", from_phone,
                )
    else:
        logger.warning(
            "Unknown reply type %s — silently skipped", type(reply).__name__,
        )


async def _find_user_by_phone(
    db: AsyncSession, from_phone: str
) -> User | None:
    """Look up a user by their WhatsApp phone across all tenants.

    Meta strips the leading ``+``; admins are inconsistent about how they
    save the number (with plus / without plus / with spaces / SA-local
    ``0`` prefix). Rather than dictate a format we normalise both sides
    to digits and compare, then also try the classic country-code
    substitutions for the tenants we know about.

    Returns the first active, non-deleted match. Any user linked to the
    number counts — the caller (bot dispatcher) handles the opt-in gate.
    """
    incoming = _digits(from_phone)
    if not incoming:
        return None

    # Build the set of DB spellings that should also match. Start with the
    # obvious variants, then add common local-vs-international swaps.
    candidates: set[str] = {
        from_phone,
        incoming,
        f"+{incoming}",
    }
    # SA: +27 vs local 0 (e.g. +27722621278 <-> 0722621278)
    if incoming.startswith("27") and len(incoming) >= 11:
        local = "0" + incoming[2:]
        candidates.update({local, f"+{local}"})
    if incoming.startswith("0") and len(incoming) >= 10:
        za_intl = "27" + incoming[1:]
        candidates.update({za_intl, f"+{za_intl}"})
    # Zimbabwe: +263 vs local 0
    if incoming.startswith("263") and len(incoming) >= 12:
        local = "0" + incoming[3:]
        candidates.update({local, f"+{local}"})

    # Direct match first — fast path when the admin stored one of the
    # obvious spellings.
    result = await db.execute(
        select(User)
        .where(
            User.whatsapp_phone.in_(list(candidates)),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
        .limit(1)
    )
    hit = result.scalar_one_or_none()
    if hit is not None:
        return hit

    # Slow path: normalise DB values in Python. Only fires when the
    # direct spellings all missed — usually because the admin typed
    # spaces / dashes ("+27 72 262 1278"). Bounded by whatsapp_phone
    # NOT NULL so it doesn't scan the whole users table.
    result = await db.execute(
        select(User).where(
            User.whatsapp_phone.is_not(None),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    for u in result.scalars().all():
        stored = _digits(u.whatsapp_phone or "")
        if not stored:
            continue
        if stored == incoming:
            return u
        # local <-> international swaps on the normalised digits
        if stored.startswith("0") and incoming.startswith("27") and stored[1:] == incoming[2:]:
            return u
        if incoming.startswith("0") and stored.startswith("27") and incoming[1:] == stored[2:]:
            return u
        if stored.startswith("0") and incoming.startswith("263") and stored[1:] == incoming[3:]:
            return u
        if incoming.startswith("0") and stored.startswith("263") and incoming[1:] == stored[3:]:
            return u
    return None


def _digits(s: str | None) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _same_number(a: str | None, b: str | None) -> bool:
    """Loose phone equality using the same normalisation as _find_user_by_phone
    (digits-only + ZA/ZW local-vs-international swap)."""
    da, db_ = _digits(a), _digits(b)
    if not da or not db_:
        return False
    if da == db_:
        return True
    # ZA
    if da.startswith("0") and db_.startswith("27") and da[1:] == db_[2:]:
        return True
    if db_.startswith("0") and da.startswith("27") and db_[1:] == da[2:]:
        return True
    # ZW
    if da.startswith("0") and db_.startswith("263") and da[1:] == db_[3:]:
        return True
    if db_.startswith("0") and da.startswith("263") and db_[1:] == da[3:]:
        return True
    return False


async def _find_user_by_regular_phone(
    db: AsyncSession, from_phone: str
) -> User | None:
    """Fallback lookup: does this WhatsApp number match ANY user's regular
    ``phone`` field?

    Used when whatsapp_phone hasn't been set — the parent's WhatsApp is
    almost always the same number as their contact phone, they just don't
    know they need to fill in a separate field. Returning them here lets
    the webhook send a one-time onboarding prompt so they can opt in.

    Only returns opted-in-eligible users (active, non-deleted). Never
    returns a user who ALSO has a different whatsapp_phone — that would
    mean they deliberately chose a different number.
    """
    incoming_digits = _digits(from_phone)
    if not incoming_digits:
        return None

    result = await db.execute(
        select(User).where(
            User.phone.is_not(None),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    for u in result.scalars().all():
        if not _same_number(u.phone, from_phone):
            continue
        # Don't shadow an explicit whatsapp_phone the user picked.
        if u.whatsapp_phone and not _same_number(u.whatsapp_phone, from_phone):
            continue
        return u
    return None


async def _send_onboarding_if_new(
    from_phone: str, user: User, db: AsyncSession
) -> bool:
    """Send the "please opt in" message, at most once per 24h per number.

    Dedupe via Redis: key ``whatsapp:onboard-sent:{digits}``, TTL 24h.
    If Redis is down we still send (better to double-message than to
    stay silent). Returns True if we sent, False if suppressed.
    """
    normalized = _digits(from_phone)
    dedupe_key = f"whatsapp:onboard-sent:{normalized}"

    # Check Redis first.
    redis_client = None
    try:
        import redis.asyncio as aioredis
        from app.config import get_settings as _get_settings
        if _get_settings().redis_url:
            redis_client = aioredis.from_url(
                _get_settings().redis_url,
                encoding="utf-8", decode_responses=True,
            )
            already = await redis_client.get(dedupe_key)
            if already:
                logger.info(
                    "Onboarding prompt already sent to %s in the last 24h",
                    from_phone,
                )
                return False
    except Exception:
        logger.exception("Redis check failed for onboarding dedupe — proceeding")

    # Send the prompt via the WhatsApp service.
    svc = await get_whatsapp_service_from_db(db)
    body = (
        f"Hi {user.first_name} 👋\n\n"
        "It looks like this WhatsApp number is registered to your ClassUp "
        "account, but you haven't turned on WhatsApp chat yet.\n\n"
        "To use ClassUp on WhatsApp:\n"
        "1. Log in at https://classup.co.za\n"
        "2. Go to *Profile*\n"
        "3. Add this number as your *WhatsApp Number* and tick "
        "*Receive WhatsApp notifications*\n"
        "4. Message us again — I'll be ready to help!"
    )
    await svc.send_text_message(to_phone=from_phone, body=body)

    # Mark as sent so we don't repeat.
    if redis_client is not None:
        try:
            await redis_client.set(dedupe_key, "1", ex=24 * 60 * 60)
        except Exception:
            logger.exception("Redis write failed for onboarding dedupe")

    logger.info(
        "Sent onboarding prompt to %s (user %s / %s)",
        from_phone, user.id, user.email,
    )
    return True
