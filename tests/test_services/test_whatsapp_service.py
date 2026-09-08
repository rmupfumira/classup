"""Tests for the WhatsApp POC — config storage + webhook plumbing.

Focused on what matters at this stage:
- get_config falls back to env vars when no DB row exists
- save_config persists + MASKED preserves secrets on round-trip
- HMAC signature uses the configured app_secret (not app_secret_key)
- Inbound message dedup on meta_message_id via UNIQUE index

Actual Meta HTTP calls are NOT tested — those are covered by
manually hitting the /admin/whatsapp-settings/test button on staging.
"""

import hashlib
import hmac
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSettings, Tenant, WhatsAppInboundMessage
from app.services import whatsapp_service
from app.services.whatsapp_service import (
    MASKED,
    WHATSAPP_SETTINGS_KEY,
    WhatsAppService,
    get_config,
    save_config,
)


@pytest_asyncio.fixture(autouse=True)
async def _reset_whatsapp_config(db: AsyncSession):
    """Wipe the whatsapp_config row before each test."""
    await db.execute(
        SystemSettings.__table__.delete().where(
            SystemSettings.key == WHATSAPP_SETTINGS_KEY
        )
    )
    await db.commit()
    yield


class TestConfigStorage:
    async def test_get_falls_back_to_env_when_no_row(self, db: AsyncSession):
        cfg = await get_config(db)
        # Env vars are empty strings by default in test env — just confirm
        # the dataclass loads without crashing and reports not-configured
        assert cfg.configured is False

    async def test_save_persists_all_fields(self, db: AsyncSession):
        await save_config(db, {
            "phone_number_id": "15551234567890",
            "business_account_id": "111222333",
            "access_token": "EAAG_test_token",
            "verify_token": "my_verify_secret",
            "app_secret": "meta_app_secret",
        })
        await db.commit()

        cfg = await get_config(db)
        assert cfg.phone_number_id == "15551234567890"
        assert cfg.business_account_id == "111222333"
        assert cfg.access_token == "EAAG_test_token"
        assert cfg.verify_token == "my_verify_secret"
        assert cfg.app_secret == "meta_app_secret"
        assert cfg.configured is True

    async def test_masked_secret_round_trip_preserves_value(self, db: AsyncSession):
        # First save with real values
        await save_config(db, {
            "phone_number_id": "15551234567890",
            "access_token": "EAAG_original",
            "verify_token": "verify_original",
            "app_secret": "app_original",
        })
        await db.commit()

        # Second save sends MASKED for both secrets (UI does this when the
        # admin edits non-secret fields without touching the secret ones)
        await save_config(db, {
            "phone_number_id": "15551234567890",
            "access_token": MASKED,
            "verify_token": "verify_updated",  # non-secret, changes
            "app_secret": MASKED,
        })
        await db.commit()

        cfg = await get_config(db)
        # Secrets preserved
        assert cfg.access_token == "EAAG_original"
        assert cfg.app_secret == "app_original"
        # Non-secret changed
        assert cfg.verify_token == "verify_updated"

    async def test_with_masked_secrets_hides_only_secrets(self, db: AsyncSession):
        await save_config(db, {
            "phone_number_id": "1555",
            "access_token": "EAAG_token",
            "verify_token": "public_verify",
            "app_secret": "secret_secret",
        })
        await db.commit()

        cfg = await get_config(db)
        masked = cfg.with_masked_secrets()
        assert masked["access_token"] == MASKED
        assert masked["app_secret"] == MASKED
        # verify_token isn't a real secret — it's what Meta hands us in
        # the handshake, and the admin needs to see the current value to
        # confirm it matches Meta's copy
        assert masked["verify_token"] == "public_verify"
        assert masked["phone_number_id"] == "1555"
        assert masked["configured"] is True


class TestWebhookSignature:
    async def test_valid_hmac_using_configured_secret(self, db: AsyncSession):
        await save_config(db, {
            "phone_number_id": "1555",
            "access_token": "t",
            "verify_token": "v",
            "app_secret": "the_meta_app_secret",
        })
        await db.commit()

        cfg = await get_config(db)
        service = WhatsAppService(cfg)

        body = b'{"entry":[{"changes":[]}]}'
        expected = hmac.new(
            b"the_meta_app_secret", body, hashlib.sha256
        ).hexdigest()
        assert service.verify_webhook_signature(body, f"sha256={expected}") is True

    async def test_wrong_signature_rejected(self, db: AsyncSession):
        await save_config(db, {
            "phone_number_id": "1555",
            "access_token": "t",
            "verify_token": "v",
            "app_secret": "the_meta_app_secret",
        })
        await db.commit()

        cfg = await get_config(db)
        service = WhatsAppService(cfg)

        body = b'{"entry":[]}'
        # signature computed with the wrong secret
        wrong = hmac.new(b"different_secret", body, hashlib.sha256).hexdigest()
        assert service.verify_webhook_signature(body, f"sha256={wrong}") is False

    async def test_missing_prefix_rejected(self, db: AsyncSession):
        cfg = await get_config(db)
        service = WhatsAppService(cfg)
        assert service.verify_webhook_signature(b"any", "no_prefix_here") is False


class TestInboundMessageDedup:
    """Meta sometimes retries webhooks — the UNIQUE index on meta_message_id
    is what stops us auto-replying twice to the same message."""

    async def test_duplicate_meta_message_id_rejected(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        # Do each insert in its own session so IntegrityError on the second
        # doesn't leave the outer fixture's session in a broken state
        # (which trips teardown).
        from app.database import get_db_context

        msg_id = f"wamid.TEST{uuid.uuid4().hex[:12]}"

        async with get_db_context() as s1:
            s1.add(WhatsAppInboundMessage(
                from_phone="27821234567",
                message_type="text",
                text="hello",
                meta_message_id=msg_id,
                raw_payload={},
            ))
            await s1.commit()

        # Second insert with the same meta_message_id must fail
        with pytest.raises(IntegrityError):
            async with get_db_context() as s2:
                s2.add(WhatsAppInboundMessage(
                    from_phone="27821234567",
                    message_type="text",
                    text="hello",  # same body — Meta's retry
                    meta_message_id=msg_id,
                    raw_payload={},
                ))
                await s2.commit()

        # Clean up so the row doesn't leak into other tests
        async with get_db_context() as s3:
            await s3.execute(
                WhatsAppInboundMessage.__table__.delete().where(
                    WhatsAppInboundMessage.meta_message_id == msg_id
                )
            )
            await s3.commit()

    async def test_null_meta_message_id_allowed_multiple_times(
        self, db: AsyncSession
    ):
        """The unique index is partial (WHERE meta_message_id IS NOT NULL),
        so rows with a null id are always OK — used for future non-message
        events we might want to log."""
        # Unique phone per run so leftover rows from previous runs
        # (this DB is shared) don't skew the count
        phone = f"27821{uuid.uuid4().hex[:8]}"
        for _ in range(3):
            db.add(WhatsAppInboundMessage(
                from_phone=phone,
                message_type="unknown",
                text=None,
                meta_message_id=None,
                raw_payload={},
            ))
        await db.commit()

        result = await db.execute(
            select(WhatsAppInboundMessage).where(
                WhatsAppInboundMessage.from_phone == phone,
                WhatsAppInboundMessage.meta_message_id.is_(None),
            )
        )
        assert len(list(result.scalars().all())) == 3

        # Clean up
        await db.execute(
            WhatsAppInboundMessage.__table__.delete().where(
                WhatsAppInboundMessage.from_phone == phone
            )
        )
        await db.commit()
