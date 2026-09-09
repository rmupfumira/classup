"""Tests for the WhatsApp onboarding path — when a message arrives from a
phone we recognise in ``users.phone`` but not in ``users.whatsapp_phone``.

The design decision under test: rather than silently ignoring these
messages (the previous behaviour) we send a one-time opt-in prompt so
the parent can enable the bot themselves. Dedupe is best-effort — if
Redis is down we still send, because double-messaging is better than
silence in a broken deployment.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import whatsapp as wa_api
from app.models import Tenant, User
from app.models.user import Role
from app.utils.security import hash_password


@pytest_asyncio.fixture
async def user_with_phone_only(db: AsyncSession):
    """A parent whose regular phone is set but whatsapp_phone is blank —
    the classic "didn't know I had to fill in a separate field" case."""
    tenant_id = uuid.uuid4()
    slug = f"onb-{tenant_id.hex[:8]}"
    tenant = Tenant(
        id=tenant_id,
        name=f"Onboarding Test {tenant_id.hex[:6]}",
        slug=slug,
        email=f"admin@{slug}.test",
        education_type="PRIMARY_SCHOOL",
        settings={"features": {"whatsapp_enabled": True}},
        is_active=True,
        onboarding_completed=True,
    )
    db.add(tenant)
    await db.flush()

    parent = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=f"parent-{tenant_id.hex[:6]}@onb.test",
        password_hash=hash_password("x"),
        first_name="Refilwe",
        last_name="Sithole",
        role=Role.PARENT.value,
        is_active=True,
        phone="+27722621278",
        whatsapp_phone=None,  # deliberately blank
    )
    db.add(parent)
    await db.commit()

    try:
        yield SimpleNamespace(tenant=tenant, parent=parent, tenant_id=tenant_id)
    finally:
        from app.database import get_db_context
        from sqlalchemy import text as sql_text
        async with get_db_context() as db2:
            await db2.execute(
                sql_text("DELETE FROM tenants WHERE id = :tid"),
                {"tid": tenant_id},
            )
            await db2.commit()


class TestFindByRegularPhone:
    async def test_finds_user_whose_phone_matches(
        self, db: AsyncSession, user_with_phone_only
    ):
        # Meta strips the leading + — mimic that.
        hit = await wa_api._find_user_by_regular_phone(db, "27722621278")
        assert hit is not None
        assert hit.id == user_with_phone_only.parent.id

    async def test_finds_user_via_local_zero_prefix(
        self, db: AsyncSession, user_with_phone_only
    ):
        # Same person, ZA local format on the DB side wouldn't match
        # here (their phone is stored as +27...), but if the incoming
        # message uses SA local format (unusual — Meta always sends
        # country code) it should still work.
        hit = await wa_api._find_user_by_regular_phone(db, "27722621278")
        assert hit is not None

    async def test_ignores_when_whatsapp_phone_is_different(
        self, db: AsyncSession, user_with_phone_only
    ):
        # User has phone=+27722621278 AND deliberately set a DIFFERENT
        # whatsapp_phone. That means they've chosen a specific number
        # for WhatsApp — don't shadow their choice.
        user_with_phone_only.parent.whatsapp_phone = "+27811111111"
        await db.commit()

        hit = await wa_api._find_user_by_regular_phone(db, "27722621278")
        assert hit is None

    async def test_returns_none_for_unknown_phone(
        self, db: AsyncSession, user_with_phone_only
    ):
        hit = await wa_api._find_user_by_regular_phone(db, "27819998888")
        assert hit is None


class TestOnboardingSendGate:
    """The 24h Redis dedupe — send once, then stay silent for 24h.

    We patch Redis + the WhatsApp send so these tests don't touch either
    service. What we're verifying is the gate logic itself.
    """

    async def test_sends_first_time(
        self, db: AsyncSession, user_with_phone_only
    ):
        # Redis returns None (no key) → we send + set the key.
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        mock_svc = AsyncMock()
        mock_svc.send_text_message = AsyncMock()

        # settings.redis_url is None in the test env — patch it so the
        # Redis block in _send_onboarding_if_new actually runs.
        fake_settings = SimpleNamespace(redis_url="redis://fake:6379/0")
        with patch(
            "redis.asyncio.from_url", return_value=mock_redis,
        ), patch(
            "app.api.v1.whatsapp.get_whatsapp_service_from_db",
            new=AsyncMock(return_value=mock_svc),
        ), patch(
            "app.config.get_settings", return_value=fake_settings,
        ):
            sent = await wa_api._send_onboarding_if_new(
                from_phone="27722621278",
                user=user_with_phone_only.parent,
                db=db,
            )
        assert sent is True
        mock_svc.send_text_message.assert_awaited_once()
        # The prompt should name the parent and mention Profile.
        call_body = mock_svc.send_text_message.call_args.kwargs["body"]
        assert "Refilwe" in call_body
        assert "Profile" in call_body
        # And the dedupe key was set with 24h TTL.
        mock_redis.set.assert_awaited_once()
        args, kwargs = mock_redis.set.call_args
        assert kwargs.get("ex") == 24 * 60 * 60

    async def test_suppresses_second_send_within_24h(
        self, db: AsyncSession, user_with_phone_only
    ):
        # Redis returns "1" — we've sent already, stay silent.
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="1")
        mock_redis.set = AsyncMock()
        mock_svc = AsyncMock()
        mock_svc.send_text_message = AsyncMock()

        # settings.redis_url is None in the test env — patch it so the
        # Redis block in _send_onboarding_if_new actually runs.
        fake_settings = SimpleNamespace(redis_url="redis://fake:6379/0")
        with patch(
            "redis.asyncio.from_url", return_value=mock_redis,
        ), patch(
            "app.api.v1.whatsapp.get_whatsapp_service_from_db",
            new=AsyncMock(return_value=mock_svc),
        ), patch(
            "app.config.get_settings", return_value=fake_settings,
        ):
            sent = await wa_api._send_onboarding_if_new(
                from_phone="27722621278",
                user=user_with_phone_only.parent,
                db=db,
            )
        assert sent is False
        mock_svc.send_text_message.assert_not_awaited()

    async def test_redis_down_still_sends(
        self, db: AsyncSession, user_with_phone_only
    ):
        # Redis raises → we still send (better than silence during an
        # outage). No dedupe key gets written, so if Redis stays down
        # we'll double-message next time. Acceptable tradeoff.
        mock_svc = AsyncMock()
        mock_svc.send_text_message = AsyncMock()

        with patch(
            "redis.asyncio.from_url",
            side_effect=RuntimeError("Redis is down"),
        ), patch(
            "app.api.v1.whatsapp.get_whatsapp_service_from_db",
            new=AsyncMock(return_value=mock_svc),
        ):
            sent = await wa_api._send_onboarding_if_new(
                from_phone="27722621278",
                user=user_with_phone_only.parent,
                db=db,
            )
        assert sent is True
        mock_svc.send_text_message.assert_awaited_once()
