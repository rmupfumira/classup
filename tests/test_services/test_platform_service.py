"""Tests for the platform defaults service.

These defaults are inherited by every new tenant on signup; the super
admin manages them via /admin/platform-settings.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSettings
from app.models.tenant import EducationType, get_default_tenant_settings
from app.services import platform_service


@pytest.fixture(autouse=True)
async def _reset_platform_row(db: AsyncSession):
    """Wipe the platform_defaults row before each test so we start clean."""
    await db.execute(
        SystemSettings.__table__.delete().where(
            SystemSettings.key == platform_service.PLATFORM_SETTINGS_KEY
        )
    )
    await db.commit()
    yield


class TestPlatformDefaults:
    async def test_get_returns_built_in_when_no_row(self, db: AsyncSession):
        d = await platform_service.get_defaults(db)
        assert d.default_currency == "ZAR"
        assert d.default_country == "ZA"
        assert d.default_language == "en"
        assert d.default_timezone == "Africa/Johannesburg"
        assert d.platform_name == "ClassUp"

    async def test_update_persists_changes(self, db: AsyncSession):
        await platform_service.update_defaults(
            db,
            {
                "default_currency": "USD",
                "default_country": "US",
                "default_language": "en",
                "default_timezone": "America/New_York",
                "platform_name": "MySchoolApp",
                "support_email": "help@myschoolapp.io",
                "support_phone": "+1 555 1234",
            },
        )
        await db.commit()

        d = await platform_service.get_defaults(db)
        assert d.default_currency == "USD"
        assert d.default_country == "US"
        assert d.default_timezone == "America/New_York"
        assert d.platform_name == "MySchoolApp"
        assert d.support_email == "help@myschoolapp.io"

    async def test_partial_update_keeps_other_values(self, db: AsyncSession):
        # Initial full save
        await platform_service.update_defaults(
            db,
            {
                "platform_name": "Acme",
                "support_email": "support@acme.com",
                "default_currency": "GBP",
                "default_country": "GB",
                "default_language": "en",
                "default_timezone": "Europe/London",
            },
        )
        await db.commit()

        # Update only the currency
        await platform_service.update_defaults(
            db, {"default_currency": "EUR"}
        )
        await db.commit()

        d = await platform_service.get_defaults(db)
        assert d.default_currency == "EUR"
        assert d.platform_name == "Acme"  # preserved
        assert d.support_email == "support@acme.com"  # preserved

    async def test_unknown_keys_are_dropped(self, db: AsyncSession):
        await platform_service.update_defaults(
            db,
            {
                "default_currency": "ZAR",
                "default_country": "ZA",
                "default_language": "en",
                "default_timezone": "Africa/Johannesburg",
                "platform_name": "ClassUp",
                "support_email": "x@y.com",
                "support_phone": "",
                "evil_key_we_dont_allow": "bobby tables",
                "another_bad_one": True,
            },
        )
        await db.commit()

        # The stored JSONB should not contain the unknown keys
        result = await db.execute(
            select(SystemSettings).where(
                SystemSettings.key == platform_service.PLATFORM_SETTINGS_KEY
            )
        )
        row = result.scalar_one()
        assert "evil_key_we_dont_allow" not in row.value
        assert "another_bad_one" not in row.value

    async def test_uppercase_normalisation(self, db: AsyncSession):
        await platform_service.update_defaults(
            db, {"default_currency": "usd", "default_country": "us"}
        )
        await db.commit()
        d = await platform_service.get_defaults(db)
        assert d.default_currency == "USD"
        assert d.default_country == "US"


class TestTenantInheritsPlatformDefaults:
    """The whole point of platform defaults: new tenants pick them up."""

    async def test_get_default_tenant_settings_uses_platform_dict(self):
        platform = {
            "default_currency": "USD",
            "default_country": "US",
            "default_language": "en",
            "default_timezone": "America/Chicago",
        }
        settings = get_default_tenant_settings(
            EducationType.PRIMARY_SCHOOL, platform_defaults=platform
        )
        assert settings["billing_currency"] == "USD"
        assert settings["country"] == "US"
        assert settings["timezone"] == "America/Chicago"
        assert settings["language"] == "en"

    async def test_get_default_tenant_settings_without_platform_uses_built_in(self):
        # Backward-compat: existing callers that don't pass platform_defaults
        # still get the original hard-coded ZAR / Joburg / en values.
        settings = get_default_tenant_settings(EducationType.PRIMARY_SCHOOL)
        assert settings["billing_currency"] == "ZAR"
        assert settings["timezone"] == "Africa/Johannesburg"
        assert settings["language"] == "en"
