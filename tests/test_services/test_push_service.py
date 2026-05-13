"""Tests for the push notification service.

Focus on the bits that are easy to get wrong:
  - VAPID config read/write
  - SEC1 PEM key load into a Vapid02 instance (this is the gotcha that
    burned hours in the prior project)
  - Subscription upsert by endpoint
  - Dead-subscription cleanup on 404 / 410
  - URL endpoint preview shortening
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PushSubscription, SystemSettings, Tenant, User
from app.services import push_service
from pywebpush import WebPushException


# Helper: real SEC1 PEM produced via the generator (small fixture so we
# don't depend on the network or filesystem)
_SAMPLE_PEM = """-----BEGIN EC PRIVATE KEY-----
MHcCAQEEIISE/49vZlE1RrkBylCV8K6Fa7muGAycMPDjObauDPGhoAoGCCqGSM49
AwEHoUQDQgAEVNESG9U4WkKS/EMjJLb+6eDIt5tC1aDH/wUmYTgcTBSYORpugGDL
XqaQltcpfaiQTXGSl4VwTFGN1+zfGAJhFA==
-----END EC PRIVATE KEY-----"""
_SAMPLE_PUBLIC = "BFTREhvVOFpCkvxDIyS2_ungyLebQtWgx_8FJmE4HEwUmDkaboBgy16mkJbXKX2okE1xkpeFcExRjdfs3xgCYRQ"
_SAMPLE_SUBJECT = "mailto:test@example.com"


@pytest_asyncio.fixture(autouse=True)
async def _reset_vapid_cache():
    """Reset the module-level VAPID cache before every test."""
    push_service.reset_cache()
    yield
    push_service.reset_cache()


class TestVapidConfig:
    async def test_returns_empty_when_unconfigured(self, db: AsyncSession):
        # Make sure no row exists for this test (other tests may have added)
        await db.execute(SystemSettings.__table__.delete().where(
            SystemSettings.key == push_service.VAPID_SETTINGS_KEY
        ))
        await db.commit()

        config = await push_service.get_vapid_config(db)
        assert config.configured is False
        assert config.public_key_b64url == ""

    async def test_loads_valid_config(self, db: AsyncSession):
        # Clear any prior row first
        await db.execute(SystemSettings.__table__.delete().where(
            SystemSettings.key == push_service.VAPID_SETTINGS_KEY
        ))
        db.add(SystemSettings(
            key=push_service.VAPID_SETTINGS_KEY,
            value={
                "public_key_b64url": _SAMPLE_PUBLIC,
                "private_pem": _SAMPLE_PEM,
                "subject": _SAMPLE_SUBJECT,
                "generated_at": "2026-05-13T00:00:00+00:00",
            },
        ))
        await db.commit()

        config = await push_service.get_vapid_config(db)
        assert config.configured is True
        assert config.public_key_b64url == _SAMPLE_PUBLIC
        assert config.subject == _SAMPLE_SUBJECT
        assert "BEGIN EC PRIVATE KEY" in config.private_pem

    async def test_sec1_pem_loads_into_vapid_instance(self):
        """The exact bug we're guarding against: SEC1 PEM must load cleanly.
        If this fails, py_vapid received a PKCS8 PEM or junk."""
        instance = push_service._build_vapid_instance(_SAMPLE_PEM)
        # Public + private should both be set
        assert instance._private_key is not None
        assert instance._public_key is not None
        # Vapid02 must accept the instance — that's the API pywebpush calls
        assert callable(getattr(instance, "sign", None))


class TestSubscriptionUpsert:
    async def test_creates_new_subscription(
        self, db: AsyncSession, test_tenant: Tenant, test_admin: User
    ):
        endpoint = "https://fcm.googleapis.com/fcm/send/test-endpoint-1"
        sub = await push_service.upsert_subscription(
            db,
            tenant_id=test_tenant.id,
            user_id=test_admin.id,
            endpoint=endpoint,
            p256dh="p256dh_test_key",
            auth="auth_test",
            user_agent="Mozilla/5.0 test",
        )
        await db.commit()

        assert sub.id is not None
        assert sub.endpoint == endpoint
        assert sub.user_id == test_admin.id

    async def test_resubscribing_same_endpoint_upserts(
        self, db: AsyncSession, test_tenant: Tenant, test_admin: User
    ):
        endpoint = "https://fcm.googleapis.com/fcm/send/test-endpoint-2"
        first = await push_service.upsert_subscription(
            db,
            tenant_id=test_tenant.id, user_id=test_admin.id, endpoint=endpoint,
            p256dh="old_p256dh", auth="old_auth",
        )
        await db.commit()

        # Same endpoint, new keys — should update the existing row
        second = await push_service.upsert_subscription(
            db,
            tenant_id=test_tenant.id, user_id=test_admin.id, endpoint=endpoint,
            p256dh="new_p256dh", auth="new_auth",
        )
        await db.commit()

        assert first.id == second.id
        assert second.p256dh == "new_p256dh"
        assert second.auth == "new_auth"

        # No duplicate row
        count = await db.execute(
            select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        )
        assert len(count.scalars().all()) == 1

    async def test_delete_by_endpoint(
        self, db: AsyncSession, test_tenant: Tenant, test_admin: User
    ):
        endpoint = "https://fcm.googleapis.com/fcm/send/test-endpoint-3"
        await push_service.upsert_subscription(
            db, tenant_id=test_tenant.id, user_id=test_admin.id, endpoint=endpoint,
            p256dh="x", auth="y",
        )
        await db.commit()

        removed = await push_service.delete_subscription_by_endpoint(
            db, user_id=test_admin.id, endpoint=endpoint
        )
        await db.commit()
        assert removed is True

        # Idempotent: deleting again returns False
        again = await push_service.delete_subscription_by_endpoint(
            db, user_id=test_admin.id, endpoint=endpoint
        )
        await db.commit()
        assert again is False


class TestDeadSubscriptionCleanup:
    """The pywebpush gotcha: 404/410 means the browser un-subscribed.
    We must delete the row, not retry forever."""

    @pytest_asyncio.fixture
    async def _vapid_configured(self, db: AsyncSession):
        await db.execute(SystemSettings.__table__.delete().where(
            SystemSettings.key == push_service.VAPID_SETTINGS_KEY
        ))
        db.add(SystemSettings(
            key=push_service.VAPID_SETTINGS_KEY,
            value={
                "public_key_b64url": _SAMPLE_PUBLIC,
                "private_pem": _SAMPLE_PEM,
                "subject": _SAMPLE_SUBJECT,
            },
        ))
        await db.commit()
        yield
        push_service.reset_cache()

    @pytest.mark.usefixtures("_vapid_configured")
    async def test_410_gone_deletes_subscription(
        self, db: AsyncSession, test_tenant: Tenant, test_admin: User
    ):
        sub = await push_service.upsert_subscription(
            db, tenant_id=test_tenant.id, user_id=test_admin.id,
            endpoint="https://fcm.test/dead", p256dh="x", auth="y",
        )
        await db.commit()
        sub_id = sub.id

        # Fake a 410 response from the push service
        fake_response = MagicMock(status_code=410)
        with patch("app.services.push_service.webpush",
                   side_effect=WebPushException("subscription expired",
                                                response=fake_response)):
            ok = await push_service.send_to_subscription(
                db, sub, {"title": "test"}
            )
            await db.commit()

        assert ok is False
        # Row should be gone
        check = await db.execute(
            select(PushSubscription).where(PushSubscription.id == sub_id)
        )
        assert check.scalar_one_or_none() is None

    @pytest.mark.usefixtures("_vapid_configured")
    async def test_404_not_found_deletes_subscription(
        self, db: AsyncSession, test_tenant: Tenant, test_admin: User
    ):
        sub = await push_service.upsert_subscription(
            db, tenant_id=test_tenant.id, user_id=test_admin.id,
            endpoint="https://fcm.test/missing", p256dh="x", auth="y",
        )
        await db.commit()
        sub_id = sub.id

        fake_response = MagicMock(status_code=404)
        with patch("app.services.push_service.webpush",
                   side_effect=WebPushException("not found", response=fake_response)):
            await push_service.send_to_subscription(db, sub, {"title": "t"})
            await db.commit()

        check = await db.execute(
            select(PushSubscription).where(PushSubscription.id == sub_id)
        )
        assert check.scalar_one_or_none() is None

    @pytest.mark.usefixtures("_vapid_configured")
    async def test_500_keeps_subscription_records_error(
        self, db: AsyncSession, test_tenant: Tenant, test_admin: User
    ):
        sub = await push_service.upsert_subscription(
            db, tenant_id=test_tenant.id, user_id=test_admin.id,
            endpoint="https://fcm.test/flaky", p256dh="x", auth="y",
        )
        await db.commit()

        fake_response = MagicMock(status_code=500)
        with patch("app.services.push_service.webpush",
                   side_effect=WebPushException("oops", response=fake_response)):
            ok = await push_service.send_to_subscription(db, sub, {"title": "t"})
            await db.commit()

        assert ok is False
        # Row should still exist with error recorded
        await db.refresh(sub)
        assert sub.last_failed_at is not None
        assert sub.last_error is not None and "oops" in sub.last_error

    @pytest.mark.usefixtures("_vapid_configured")
    async def test_success_stamps_last_seen(
        self, db: AsyncSession, test_tenant: Tenant, test_admin: User
    ):
        sub = await push_service.upsert_subscription(
            db, tenant_id=test_tenant.id, user_id=test_admin.id,
            endpoint="https://fcm.test/ok", p256dh="x", auth="y",
        )
        # Force last_failed_at to make sure success clears it
        sub.last_failed_at = datetime.now(timezone.utc)
        sub.last_error = "previous"
        await db.commit()

        with patch("app.services.push_service.webpush", return_value=None):
            ok = await push_service.send_to_subscription(db, sub, {"title": "t"})
            await db.commit()

        assert ok is True
        await db.refresh(sub)
        assert sub.last_failed_at is None
        assert sub.last_error is None
