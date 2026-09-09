"""Tests for the WhatsApp bot mode resolver + dispatch + admin endpoint.

Covers Phase 2A wiring:
- resolve_bot_mode truth table across (plan_features x tenant.features)
- handle_inbound_message returns the resolved mode + a reply for MENU/AI
  and (mode, None) for OFF so the caller knows to stay silent.
- The super admin PUT /api/v1/admin/tenants/{id}/features endpoint merges
  correctly and enforces the plan-gated opt-in for the WhatsApp features.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin import (
    TenantFeaturesUpdateRequest,
    update_tenant_features,
)
from app.models import Tenant, User
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


def _user(first_name: str = "Alice", tenant_id: uuid.UUID | None = None) -> User:
    """Lightweight User stand-in — dispatch only reads first_name + id + role."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        first_name=first_name,
        last_name="Parent",
        role="PARENT",
    )  # type: ignore[return-value]


class TestHandleInboundMessage:
    """Dispatcher wraps the resolver with a plan-features fetch. In MENU
    mode it delegates to the state machine (patched here to isolate);
    in AI mode it still returns a placeholder in Phase 2B."""

    async def test_off_returns_none_reply(self, db: AsyncSession):
        # No plan features — resolver returns OFF, no reply.
        with patch(
            "app.services.whatsapp_bot._load_plan_features",
            return_value=None,
        ):
            mode, reply = await handle_inbound_message(
                db=db,
                tenant=_tenant({"whatsapp_enabled": True}),  # type: ignore[arg-type]
                user=_user(),
                from_phone="27821234567",
                text="hi",
                interactive_id=None,
            )
        assert mode is BotMode.OFF
        assert reply is None

    async def test_menu_delegates_to_state_machine(self, db: AsyncSession):
        # MENU mode should call handle_menu_message. Patch it so this test
        # doesn't hit the tools layer (that's covered in menu-bot tests).
        from app.services.whatsapp_menu_bot import TextReply
        sentinel = TextReply(body="from state machine")

        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": False})
        with patch(
            "app.services.whatsapp_bot._load_plan_features",
            return_value={"whatsapp_enabled": True, "whatsapp_ai_enabled": False},
        ), patch(
            "app.services.whatsapp_menu_bot.handle_menu_message",
            new=AsyncMock(return_value=sentinel),
        ) as mock_menu:
            mode, reply = await handle_inbound_message(
                db=db,
                tenant=tenant,  # type: ignore[arg-type]
                user=_user(),
                from_phone="27821234567",
                text="hi",
                interactive_id=None,
            )
        assert mode is BotMode.MENU
        assert reply is sentinel
        mock_menu.assert_awaited_once()

    async def test_ai_returns_placeholder_reply(self, db: AsyncSession):
        # Phase 2C hasn't landed yet — AI still returns a canned message
        # so a tenant that opts into AI early doesn't get radio silence.
        from app.services.whatsapp_menu_bot import TextReply
        tenant = _tenant({"whatsapp_enabled": True, "whatsapp_ai_enabled": True})
        with patch(
            "app.services.whatsapp_bot._load_plan_features",
            return_value={"whatsapp_enabled": True, "whatsapp_ai_enabled": True},
        ):
            mode, reply = await handle_inbound_message(
                db=db,
                tenant=tenant,  # type: ignore[arg-type]
                user=_user(first_name="Bob"),
                from_phone="27821234567",
                text="what is my balance",
                interactive_id=None,
            )
        assert mode is BotMode.AI
        assert isinstance(reply, TextReply)
        assert "Bob" in reply.body
        assert "AI chat" in reply.body

    async def test_unknown_sender_returns_off(self, db: AsyncSession):
        # Sender phone didn't match any user → user=None → OFF, silent.
        # Even if plan+tenant would resolve to MENU, we can't dispatch
        # without knowing whose data to fetch.
        tenant = _tenant({"whatsapp_enabled": True})
        with patch(
            "app.services.whatsapp_bot._load_plan_features",
            return_value={"whatsapp_enabled": True},
        ):
            mode, reply = await handle_inbound_message(
                db=db,
                tenant=tenant,  # type: ignore[arg-type]
                user=None,
                from_phone="27821234567",
                text="hi",
                interactive_id=None,
            )
        assert mode is BotMode.OFF
        assert reply is None


@pytest.fixture
def _as_super_admin():
    """Set the super-admin context so @require_super_admin() lets us in when
    calling the endpoint handler directly (no HTTP client / no auth middleware
    in these tests)."""
    import uuid as _uuid
    from app.utils.tenant_context import _current_user_id, _current_user_role

    uid_tok = _current_user_id.set(_uuid.uuid4())
    role_tok = _current_user_role.set("SUPER_ADMIN")
    try:
        yield
    finally:
        _current_user_role.reset(role_tok)
        _current_user_id.reset(uid_tok)


@pytest.mark.usefixtures("_as_super_admin")
class TestAdminFeaturesEndpoint:
    """Super admin PUT /api/v1/admin/tenants/{id}/features — the endpoint
    the tenant-edit page calls. Must merge (never wipe other features) and
    must enforce plan-gated opt-in the same way the tenant admin page does.
    """

    async def _make_tenant(self, db: AsyncSession, features: dict) -> Tenant:
        import uuid as _uuid
        tid = _uuid.uuid4()
        t = Tenant(
            id=tid,
            name=f"WA Bot Test {tid.hex[:6]}",
            slug=f"wabot-{tid.hex[:8]}",
            email=f"admin@wabot-{tid.hex[:8]}.test",
            education_type="PRIMARY_SCHOOL",
            settings={"features": features, "education_type": "PRIMARY_SCHOOL"},
            is_active=True,
            onboarding_completed=True,
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t

    def _mock_plan(self, plan_features: dict):
        """Patch subscription lookup to return a plan with the given features."""
        plan = SimpleNamespace(features=plan_features, name="Test Plan")
        sub = SimpleNamespace(plan=plan)
        svc = SimpleNamespace(get_tenant_subscription=AsyncMock(return_value=sub))
        return patch(
            "app.services.subscription_service.get_subscription_service",
            return_value=svc,
        )

    async def test_merge_preserves_unrelated_features(self, db: AsyncSession):
        # A tenant with lots of features on. Toggling WhatsApp on must
        # NOT wipe billing, attendance, etc. — the classic bug when a
        # partial update replaces the whole nested dict.
        tenant = await self._make_tenant(db, {
            "billing": True,
            "attendance_tracking": True,
            "messaging": True,
        })
        try:
            with self._mock_plan({"whatsapp_enabled": True, "whatsapp_ai_enabled": True}):
                resp = await update_tenant_features(
                    tenant_id=tenant.id,
                    request=TenantFeaturesUpdateRequest(
                        features={"whatsapp_enabled": True}
                    ),
                    db=db,
                )
            assert resp.status == "success"
            assert resp.data["features"]["billing"] is True
            assert resp.data["features"]["attendance_tracking"] is True
            assert resp.data["features"]["messaging"] is True
            assert resp.data["features"]["whatsapp_enabled"] is True
        finally:
            await db.delete(tenant)
            await db.commit()

    async def test_opt_in_gated_by_plan(self, db: AsyncSession):
        # Even super admin can't force WhatsApp on if the tenant's plan
        # doesn't include it — the request is silently coerced to False.
        # This keeps billing / cost accountability honest.
        tenant = await self._make_tenant(db, {"billing": True})
        try:
            with self._mock_plan({"whatsapp_enabled": False}):
                resp = await update_tenant_features(
                    tenant_id=tenant.id,
                    request=TenantFeaturesUpdateRequest(
                        features={"whatsapp_enabled": True, "whatsapp_ai_enabled": True}
                    ),
                    db=db,
                )
            assert resp.data["features"]["whatsapp_enabled"] is False
            assert resp.data["features"]["whatsapp_ai_enabled"] is False
        finally:
            await db.delete(tenant)
            await db.commit()

    async def test_non_optin_feature_is_not_plan_gated(self, db: AsyncSession):
        # Non-opt-in features (e.g. accounting) are super admin's call —
        # they don't need to be listed in the plan to be toggled here.
        # (Different from tenant admin page, where plan-locked features
        # are read-only. This is a super-admin bypass for support.)
        tenant = await self._make_tenant(db, {})
        try:
            with self._mock_plan({}):
                resp = await update_tenant_features(
                    tenant_id=tenant.id,
                    request=TenantFeaturesUpdateRequest(
                        features={"accounting": True}
                    ),
                    db=db,
                )
            assert resp.data["features"]["accounting"] is True
        finally:
            await db.delete(tenant)
            await db.commit()

    async def test_tenant_persists_across_reload(self, db: AsyncSession):
        # Round-trip: after the endpoint returns, re-loading the tenant in
        # a FRESH session shows the same features. Guards against forgetting
        # the commit or accidentally mutating a copy.
        from app.database import get_db_context

        tenant = await self._make_tenant(db, {"attendance_tracking": True})
        tenant_id = tenant.id
        try:
            with self._mock_plan({"whatsapp_enabled": True}):
                await update_tenant_features(
                    tenant_id=tenant_id,
                    request=TenantFeaturesUpdateRequest(
                        features={"whatsapp_enabled": True}
                    ),
                    db=db,
                )
            # Fresh session — proves the commit landed on disk, not just in
            # the current transaction's identity map.
            async with get_db_context() as db2:
                reloaded = await db2.get(Tenant, tenant_id)
                assert reloaded is not None
                features = (reloaded.settings or {}).get("features") or {}
                assert features.get("whatsapp_enabled") is True
                assert features.get("attendance_tracking") is True
        finally:
            async with get_db_context() as db2:
                t = await db2.get(Tenant, tenant_id)
                if t is not None:
                    await db2.delete(t)
                    await db2.commit()
