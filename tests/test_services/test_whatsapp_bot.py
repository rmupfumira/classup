"""Tests for the WhatsApp bot mode resolver + dispatch.

Covers Phase 2A wiring:
- resolve_bot_mode truth table across (plan_features x tenant.features)
- handle_inbound_message returns the resolved mode + a reply for MENU/AI
  and (mode, None) for OFF so the caller knows to stay silent.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant
from app.services.whatsapp_bot import (
    BotMode,
    handle_inbound_message,
    resolve_bot_mode,
)


def _tenant(features: dict | None) -> Tenant:
    """Build a lightweight Tenant stand-in for the resolver — no DB round-trip
    needed. Only ``settings.features`` is read."""
    return SimpleNamespace(
        id=None,
        settings={"features": features or {}},
    )  # type: ignore[return-value]


class TestResolveBotMode:
    """The core truth table. Both plan and tenant have to agree; opt-in at
    each level is a deliberate design choice — the tenant admin must
    consciously flip the switch even after their plan allows it."""

    def test_no_tenant_returns_off(self):
        assert resolve_bot_mode(None, {"whatsapp_enabled": True, "whatsapp_ai_enabled": True}) is BotMode.OFF

    def test_no_plan_features_returns_off(self):
        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": True})
        assert resolve_bot_mode(tenant, None) is BotMode.OFF
        assert resolve_bot_mode(tenant, {}) is BotMode.OFF

    def test_plan_allows_but_tenant_not_opted_in_returns_off(self):
        # Plan is generous, tenant hasn't turned anything on — silent.
        # This is the important opt-in guarantee: if a school gets bumped
        # to a plan with WhatsApp, we do NOT auto-start replying to parents.
        tenant = _tenant({"whatsapp_enabled": False, "whatsapp_ai_enabled": False})
        plan = {"whatsapp_enabled": True, "whatsapp_ai_enabled": True}
        assert resolve_bot_mode(tenant, plan) is BotMode.OFF

    def test_tenant_wants_it_but_plan_disallows_returns_off(self):
        # Tenant somehow has the flag on (previous plan, manual DB edit) but
        # the current plan doesn't allow it — plan wins, silent.
        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": True})
        plan = {"whatsapp_enabled": False, "whatsapp_ai_enabled": False}
        assert resolve_bot_mode(tenant, plan) is BotMode.OFF

    def test_whatsapp_only_returns_menu(self):
        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": False})
        plan = {"whatsapp_enabled": True, "whatsapp_ai_enabled": False}
        assert resolve_bot_mode(tenant, plan) is BotMode.MENU

    def test_ai_plan_but_tenant_ai_off_returns_menu(self):
        # The upsell case — plan allows AI but school decided menu is enough.
        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": False})
        plan = {"whatsapp_enabled": True, "whatsapp_ai_enabled": True}
        assert resolve_bot_mode(tenant, plan) is BotMode.MENU

    def test_ai_tenant_but_plan_ai_off_returns_menu(self):
        # Tenant wants AI but plan doesn't include it — falls back to MENU
        # (they still get WhatsApp, just not the AI upgrade).
        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": True})
        plan = {"whatsapp_enabled": True, "whatsapp_ai_enabled": False}
        assert resolve_bot_mode(tenant, plan) is BotMode.MENU

    def test_both_on_at_both_levels_returns_ai(self):
        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": True})
        plan = {"whatsapp_enabled": True, "whatsapp_ai_enabled": True}
        assert resolve_bot_mode(tenant, plan) is BotMode.AI

    def test_missing_keys_default_to_false(self):
        # Real-world case: an older plan/tenant that predates these flags.
        # Missing key must be treated as False, not raise KeyError.
        tenant = _tenant({})
        assert resolve_bot_mode(tenant, {}) is BotMode.OFF
        tenant = _tenant({"whatsapp_enabled": True})
        assert resolve_bot_mode(tenant, {"whatsapp_enabled": True}) is BotMode.MENU

    def test_tenant_with_no_settings_returns_off(self):
        tenant = SimpleNamespace(id=None, settings=None)
        assert resolve_bot_mode(tenant, {"whatsapp_enabled": True}) is BotMode.OFF  # type: ignore[arg-type]


class TestHandleInboundMessage:
    """Dispatcher wraps the resolver with a plan-features fetch and returns
    the reply text (or None). Actual send is done by the caller."""

    async def test_off_returns_none_reply(self, db: AsyncSession):
        # No plan features — resolver returns OFF, no reply text.
        with patch(
            "app.services.whatsapp_bot._load_plan_features",
            return_value=None,
        ):
            mode, reply = await handle_inbound_message(
                db=db,
                tenant=_tenant({"whatsapp_enabled": True}),  # type: ignore[arg-type]
                from_phone="27821234567",
                text="hi",
                matched_user_name="Alice",
            )
        assert mode is BotMode.OFF
        assert reply is None

    async def test_menu_returns_placeholder_reply(self, db: AsyncSession):
        # Phase 2A placeholder — MENU returns a canned message. Replaced in 2B.
        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": False})
        with patch(
            "app.services.whatsapp_bot._load_plan_features",
            return_value={"whatsapp_enabled": True, "whatsapp_ai_enabled": False},
        ):
            mode, reply = await handle_inbound_message(
                db=db,
                tenant=tenant,  # type: ignore[arg-type]
                from_phone="27821234567",
                text="hi",
                matched_user_name="Alice",
            )
        assert mode is BotMode.MENU
        assert reply is not None
        assert "Alice" in reply
        assert "menu-driven" in reply  # placeholder wording gate

    async def test_ai_returns_placeholder_reply(self, db: AsyncSession):
        # Phase 2A placeholder — AI returns a canned message. Replaced in 2C.
        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": True})
        with patch(
            "app.services.whatsapp_bot._load_plan_features",
            return_value={"whatsapp_enabled": True, "whatsapp_ai_enabled": True},
        ):
            mode, reply = await handle_inbound_message(
                db=db,
                tenant=tenant,  # type: ignore[arg-type]
                from_phone="27821234567",
                text="what is my balance",
                matched_user_name="Alice",
            )
        assert mode is BotMode.AI
        assert reply is not None
        assert "Alice" in reply
        assert "AI chat" in reply  # placeholder wording gate

    async def test_unknown_sender_returns_off(self, db: AsyncSession):
        # Sender phone didn't match any user → tenant=None → OFF, silent.
        mode, reply = await handle_inbound_message(
            db=db,
            tenant=None,
            from_phone="27821234567",
            text="hi",
            matched_user_name=None,
        )
        assert mode is BotMode.OFF
        assert reply is None
