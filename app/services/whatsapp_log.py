"""Outbound WhatsApp persistence helper.

Two callers use this:
- ``app/api/v1/whatsapp.py`` — bot reply funnel (``_send_reply``).
- ``app/services/parent_notifier.py`` — admin-triggered notification
  dispatcher (``_send_template``).

Between them we cover every send that constitutes a "message the
parent sees" — attendance alerts, invoices, event reminders, and
the bot's inbound-reply chain. Ops sends like the super admin
test-send are intentionally not logged (they never reach real
parents through the app's conversation flow).

The helper is best-effort: a persistence failure NEVER blocks or
propagates back to the caller. Failing loudly on a DB write from
inside a fire-and-forget notification path would poison the
notification's own error handling — the caller doesn't want to
know.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WhatsAppOutboundMessage

logger = logging.getLogger(__name__)


def _extract_meta_message_id(response: dict | None) -> str | None:
    """Meta's send response shape is ``{messages: [{id: '...'}]}``."""
    if not response:
        return None
    try:
        messages = response.get("messages") or []
        if messages and isinstance(messages, list):
            return (messages[0] or {}).get("id")
    except (AttributeError, IndexError, TypeError):
        pass
    return None


async def record_outbound(
    db: AsyncSession,
    *,
    to_phone: str,
    message_type: str,
    body_text: str | None,
    template_name: str | None = None,
    tenant_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    inbound_message_id: uuid.UUID | None = None,
    sent_by_user_id: uuid.UUID | None = None,
    response: dict | None = None,
    error: str | None = None,
) -> None:
    """Insert one outbound row. Never raises.

    ``response`` is the raw dict Meta returns from a successful send —
    we extract ``meta_message_id`` from it. Pass ``response=None`` +
    ``error="..."`` for a failed send so the row still lands (so
    admins can see WHY a message never went out).
    """
    try:
        db.add(WhatsAppOutboundMessage(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            target_user_id=target_user_id,
            to_phone=(to_phone or "").lstrip("+"),
            message_type=message_type,
            template_name=template_name,
            body_text=(body_text or "")[:8000] if body_text else None,
            meta_message_id=_extract_meta_message_id(response),
            inbound_message_id=inbound_message_id,
            error=(error or None) if error else None,
            sent_by_user_id=sent_by_user_id,
        ))
        # No flush() here — let the caller's own commit/flush push
        # the row. Awaiting a flush from inside a fire-and-forget
        # helper can hang when the caller's session is mid-transaction
        # or being rolled back (which happens in test fixtures that
        # use nested SAVEPOINTs).
    except Exception:
        # Best-effort — this hook must not disturb the send flow.
        logger.exception(
            "Failed to persist outbound WhatsApp log for %s (type=%s, template=%s)",
            to_phone, message_type, template_name,
        )


def summarise_reply(reply: Any) -> tuple[str, str | None]:
    """Turn a Reply-shaped bot response into (message_type, body_text).

    Handles the reply types the bot returns:
    - ``TextReply`` — text
    - ``ButtonReply`` — interactive_buttons (body + button labels)
    - ``ListReply`` — interactive_list (body + row titles)
    - ``MultiReply`` — first sub-reply's summary; downstream calls
      capture each part separately

    Unknown shapes fall back to ``("text", str(reply))``.
    """
    # Introspect by attribute rather than isinstance so this helper
    # doesn't have to import every reply dataclass and create a
    # circular dep with whatsapp_bot.
    text = getattr(reply, "body", None) or getattr(reply, "text", None) or ""
    buttons = getattr(reply, "buttons", None)
    rows = getattr(reply, "sections", None)
    if buttons:
        titles = ", ".join(b.get("title", "") for b in buttons)
        return "interactive_buttons", f"{text}\n[buttons: {titles}]".strip()
    if rows:
        titles: list[str] = []
        for sec in rows:
            for r in sec.get("rows", []) or []:
                titles.append(r.get("title", ""))
        return "interactive_list", f"{text}\n[list: {', '.join(titles)}]".strip()
    return "text", text or None
