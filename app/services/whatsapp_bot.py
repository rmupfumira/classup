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

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant

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
    from_phone: str,
    text: str | None,
    matched_user_name: str | None,
) -> tuple[BotMode, str | None]:
    """Dispatch an inbound WhatsApp message to the right mode handler.

    Returns the resolved mode + the reply text to send (or ``None`` for OFF).
    Actual send is done by the caller (which owns the WhatsApp API client).

    Phase 2A: MENU and AI return placeholder text. Phase 2B replaces MENU
    with the real state-machine bot; Phase 2C replaces AI with the Claude
    agent loop.
    """
    tenant_id = tenant.id if tenant else None
    plan_features = await _load_plan_features(db, tenant_id)
    mode = resolve_bot_mode(tenant, plan_features)

    logger.info(
        "WhatsApp bot dispatch: tenant=%s user=%s mode=%s",
        tenant_id, from_phone, mode.value,
    )

    if mode is BotMode.OFF:
        return mode, None

    name = matched_user_name or "there"
    if mode is BotMode.MENU:
        reply = (
            f"Hi {name} 👋 ClassUp WhatsApp is being set up here — the "
            "menu-driven chat will be live soon. In the meantime, log in at "
            "https://classup.co.za to check attendance, balances, and reports."
        )
    else:  # AI
        reply = (
            f"Hi {name} 👋 ClassUp AI chat is being set up here — natural "
            "conversation with the school system will be live soon. For now, "
            "log in at https://classup.co.za to check attendance, balances, "
            "and reports."
        )
    return mode, reply
