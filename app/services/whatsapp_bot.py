"""WhatsApp bot logic — mode resolution + inbound-message dispatch.

Layered on top of the low-level `whatsapp_service` (which handles the Meta
API). This module owns the *what should the bot do?* question:

  - Look up the sender's tenant + subscription plan
  - Resolve one of three modes: OFF, MENU, AI
  - Dispatch to the mode's handler

The three modes:

  OFF   Plan doesn't include WhatsApp, or tenant hasn't opted in.
        Message is still logged (webhook.py has done that already);
        no reply is sent. Deliberately silent so we don't surprise
        parents who never signed up.

  MENU  Plan includes WhatsApp, tenant opted in, but AI is off (either
        the plan doesn't include AI, or the tenant chose not to enable
        it). Uses the state-machine bot with WhatsApp Interactive
        Messages (menu buttons + lists). Cheap, predictable, zero LLM
        cost — some schools will always prefer it.

  AI    Plan includes AI, tenant opted in. Uses Claude with tool use
        for natural conversation.

Phase 2A ships the plumbing only — MENU and AI both send a placeholder
canned reply, replaced in phases 2B and 2C respectively.
"""

from __future__ import annotations

import logging
import uuid
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant

if TYPE_CHECKING:
    from app.models import User
    from app.services.whatsapp_menu_bot import MenuResponse

logger = logging.getLogger(__name__)


class BotMode(str, Enum):
    """Effective WhatsApp bot mode for one tenant."""

    OFF = "OFF"
    MENU = "MENU"
    AI = "AI"


# Features that follow opt-in semantics: the plan gates AVAILABILITY, but
# the tenant admin must consciously enable them. Never auto-enabled from a
# plan upgrade — this is the "no surprise WhatsApp replies to parents"
# guarantee.
OPTIN_FEATURES: frozenset[str] = frozenset({
    "whatsapp_enabled",
    "whatsapp_ai_enabled",
})

_PLAN_WHATSAPP_KEY = "whatsapp_enabled"
_PLAN_AI_KEY = "whatsapp_ai_enabled"
_TENANT_WHATSAPP_KEY = "whatsapp_enabled"
_TENANT_AI_KEY = "whatsapp_ai_enabled"


def resolve_bot_mode(
    tenant: Tenant | None,
    plan_features: dict | None,
) -> BotMode:
    """Return the effective bot mode for a tenant.

    Args:
        tenant: The tenant whose sender the message is from. ``None`` when the
            sender phone doesn't match any known user — we go silent.
        plan_features: The tenant's subscription plan features dict. ``None``
            or empty when the tenant has no active subscription — also silent.

    Rules (both plan + tenant must agree):
        - OFF  if plan doesn't allow WhatsApp *or* tenant hasn't opted in
        - AI   if plan allows AI *and* tenant enabled AI (implies WhatsApp on)
        - MENU otherwise (WhatsApp on but AI off at either level)

    Design: opt-in at both levels means we never surprise a parent by
    replying to their unrelated WhatsApp message just because the school
    happened to add ClassUp — a school admin must deliberately enable it.
    """
    if tenant is None:
        return BotMode.OFF

    pf = plan_features or {}
    tf = (tenant.settings or {}).get("features") or {}

    plan_allows_whatsapp = bool(pf.get(_PLAN_WHATSAPP_KEY, False))
    tenant_wants_whatsapp = bool(tf.get(_TENANT_WHATSAPP_KEY, False))
    if not (plan_allows_whatsapp and tenant_wants_whatsapp):
        return BotMode.OFF

    plan_allows_ai = bool(pf.get(_PLAN_AI_KEY, False))
    tenant_wants_ai = bool(tf.get(_TENANT_AI_KEY, False))
    if plan_allows_ai and tenant_wants_ai:
        return BotMode.AI

    return BotMode.MENU


async def _load_plan_features(
    db: AsyncSession, tenant_id: uuid.UUID | None
) -> dict | None:
    """Fetch the tenant's plan features, or None if no active subscription."""
    if tenant_id is None:
        return None
    try:
        from app.services.subscription_service import get_subscription_service

        sub = await get_subscription_service().get_tenant_subscription(db, tenant_id)
    except Exception:
        logger.exception("Failed to load subscription for tenant %s", tenant_id)
        return None
    if sub and sub.plan and sub.plan.features:
        return sub.plan.features
    return None


async def handle_inbound_message(
    db: AsyncSession,
    tenant: Tenant | None,
    user: "User | None",
    from_phone: str,
    text: str | None,
    interactive_id: str | None,
) -> "tuple[BotMode, MenuResponse | None]":
    """Dispatch an inbound WhatsApp message to the right mode handler.

    Returns the resolved mode + the reply to send (a ``TextReply`` /
    ``ButtonReply`` / ``ListReply`` from whatsapp_menu_bot). ``None``
    means "do not send anything" — OFF mode, or an unmatched sender.

    Phase 2B: MENU delegates to the whatsapp_menu_bot state machine.
    Phase 2C: AI will delegate to a Claude tool-use loop; today it still
    returns a placeholder text reply.
    """
    tenant_id = tenant.id if tenant else None
    plan_features = await _load_plan_features(db, tenant_id)
    mode = resolve_bot_mode(tenant, plan_features)

    logger.info(
        "WhatsApp bot dispatch: tenant=%s user=%s mode=%s interactive=%s",
        tenant_id, from_phone, mode.value, interactive_id,
    )

    if mode is BotMode.OFF:
        return mode, None

    # Sender didn't match a User in the tenant — even in an on-mode we
    # can't tell whose data to fetch, so stay silent.
    if user is None:
        return BotMode.OFF, None

    if mode is BotMode.MENU:
        # Lazy import — whatsapp_menu_bot imports whatsapp_bot_tools which
        # pulls in every model; keep it out of the module-level graph.
        from app.services.whatsapp_menu_bot import handle_menu_message
        reply = await handle_menu_message(
            db=db, user=user, text=text or "", interactive_id=interactive_id,
        )
        return mode, reply

    # AI mode. If Anthropic isn't configured yet (admin hasn't set the
    # API key), silently downgrade to menu mode so the parent still gets
    # a useful reply. If the parent tapped an interactive button, defer
    # to the menu bot for that turn too — button IDs aren't natural-
    # language input, and the AI would have to guess at the intent.
    from app.services.ai_config import get_config as get_ai_config
    from app.services.whatsapp_ai_bot import handle_ai_message
    from app.services.whatsapp_menu_bot import handle_menu_message

    ai_cfg = await get_ai_config(db)
    if not ai_cfg.configured or interactive_id:
        reply = await handle_menu_message(
            db=db, user=user, text=text or "", interactive_id=interactive_id,
        )
        return mode, reply

    reply = await handle_ai_message(
        db=db, user=user, tenant_name=tenant.name if tenant else "your school",
        text=text or "", cfg=ai_cfg,
    )
    return mode, reply


