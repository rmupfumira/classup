"""Platform-wide defaults — read/write the super-admin-managed settings
that act as fallbacks for new tenants.

Stored in ``system_settings.platform_defaults`` as a JSONB blob. Anything
not explicitly set falls through to the BUILT_IN_DEFAULTS below.

Used by:
  - ``tenant_service.create_tenant()`` — applies these as the base settings
    for any new tenant
  - super admin UI (``/admin/platform-settings``) — read + write
  - Email / template helpers that need a sensible "platform" value when no
    tenant context is available (e.g. emails sent before tenant assignment)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSettings

logger = logging.getLogger(__name__)

PLATFORM_SETTINGS_KEY = "platform_defaults"


# Sensible fallbacks if the row doesn't exist yet. Keep these matching the
# values that previously lived hard-coded in tenant.py's
# get_default_tenant_settings() so existing tenants don't see behaviour change.
BUILT_IN_DEFAULTS: dict[str, Any] = {
    "platform_name": "ClassUp",
    "support_email": "support@classup.co.za",
    "support_phone": "",
    # Locale & money — what new tenants inherit on signup
    "default_currency": "ZAR",     # ISO 4217
    "default_country": "ZA",       # ISO 3166 alpha-2
    "default_language": "en",      # must be in app.config.supported_languages
    "default_timezone": "Africa/Johannesburg",  # IANA tz
}

# What we accept on write. Anything else gets ignored. Keeps the API
# tight — admins can't sneak arbitrary keys into the JSONB blob.
ALLOWED_KEYS: set[str] = set(BUILT_IN_DEFAULTS.keys())


@dataclass(frozen=True)
class PlatformDefaults:
    """Read-side view returned by get_defaults()."""
    platform_name: str
    support_email: str
    support_phone: str
    default_currency: str
    default_country: str
    default_language: str
    default_timezone: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlatformDefaults":
        merged = {**BUILT_IN_DEFAULTS, **(d or {})}
        return cls(
            platform_name=str(merged.get("platform_name", "ClassUp")).strip() or "ClassUp",
            support_email=str(merged.get("support_email", "") or "").strip(),
            support_phone=str(merged.get("support_phone", "") or "").strip(),
            default_currency=str(merged.get("default_currency", "ZAR")).upper().strip() or "ZAR",
            default_country=str(merged.get("default_country", "ZA")).upper().strip() or "ZA",
            default_language=str(merged.get("default_language", "en")).lower().strip() or "en",
            default_timezone=str(merged.get("default_timezone", "Africa/Johannesburg")).strip() or "Africa/Johannesburg",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_name": self.platform_name,
            "support_email": self.support_email,
            "support_phone": self.support_phone,
            "default_currency": self.default_currency,
            "default_country": self.default_country,
            "default_language": self.default_language,
            "default_timezone": self.default_timezone,
        }


async def get_defaults(db: AsyncSession) -> PlatformDefaults:
    """Return platform defaults, falling back to BUILT_IN_DEFAULTS for any
    key not yet set. Cheap — single row lookup."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == PLATFORM_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if not row or not row.value:
        return PlatformDefaults.from_dict({})
    return PlatformDefaults.from_dict(row.value)


async def update_defaults(
    db: AsyncSession, updates: dict[str, Any]
) -> PlatformDefaults:
    """Merge ``updates`` into the existing platform defaults.

    Unknown keys are silently dropped (don't let admins shove arbitrary
    JSON into system_settings via the API). Empty strings ARE preserved
    so an admin can deliberately clear an optional field like support_phone.
    """
    # Filter to allowed keys + normalise values to strings (jsonb handles it)
    clean = {k: v for k, v in (updates or {}).items() if k in ALLOWED_KEYS}

    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == PLATFORM_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if row:
        merged = {**(row.value or {}), **clean}
        row.value = merged
    else:
        # First-ever write — start from built-in defaults so we get all keys
        merged = {**BUILT_IN_DEFAULTS, **clean}
        row = SystemSettings(key=PLATFORM_SETTINGS_KEY, value=merged)
        db.add(row)
    await db.flush()
    logger.info(f"Platform defaults updated: keys={sorted(clean.keys())}")
    return PlatformDefaults.from_dict(merged)


# ──────────────── Static reference data for the admin UI ────────────────

# Common ISO 4217 currency codes with display name. Used by the dropdown.
# Order: ZAR first (primary market), then USD/EUR/GBP, then African
# currencies, then a sprinkling of others. Add more as needed.
SUPPORTED_CURRENCIES: list[dict[str, str]] = [
    {"code": "ZAR", "name": "South African Rand (R)"},
    {"code": "USD", "name": "US Dollar ($)"},
    {"code": "EUR", "name": "Euro (€)"},
    {"code": "GBP", "name": "British Pound (£)"},
    {"code": "KES", "name": "Kenyan Shilling (KSh)"},
    {"code": "NGN", "name": "Nigerian Naira (₦)"},
    {"code": "GHS", "name": "Ghanaian Cedi (GH₵)"},
    {"code": "BWP", "name": "Botswana Pula (P)"},
    {"code": "ZMW", "name": "Zambian Kwacha (K)"},
    {"code": "MWK", "name": "Malawian Kwacha (MK)"},
    {"code": "NAD", "name": "Namibian Dollar (N$)"},
    {"code": "AUD", "name": "Australian Dollar (A$)"},
    {"code": "CAD", "name": "Canadian Dollar (C$)"},
    {"code": "INR", "name": "Indian Rupee (₹)"},
]

# ISO 3166 alpha-2 + display name. Keep aligned with where ClassUp is sold.
SUPPORTED_COUNTRIES: list[dict[str, str]] = [
    {"code": "ZA", "name": "South Africa"},
    {"code": "BW", "name": "Botswana"},
    {"code": "NA", "name": "Namibia"},
    {"code": "ZM", "name": "Zambia"},
    {"code": "ZW", "name": "Zimbabwe"},
    {"code": "MW", "name": "Malawi"},
    {"code": "KE", "name": "Kenya"},
    {"code": "NG", "name": "Nigeria"},
    {"code": "GH", "name": "Ghana"},
    {"code": "UG", "name": "Uganda"},
    {"code": "TZ", "name": "Tanzania"},
    {"code": "GB", "name": "United Kingdom"},
    {"code": "US", "name": "United States"},
    {"code": "AU", "name": "Australia"},
    {"code": "CA", "name": "Canada"},
    {"code": "IN", "name": "India"},
]

# Supported app languages — must match translations/{lang}/messages.json
SUPPORTED_LANGUAGES: list[dict[str, str]] = [
    {"code": "en", "name": "English"},
    {"code": "af", "name": "Afrikaans"},
]

# Common timezones — admins on edge cases can ignore the dropdown and use
# the text alternative (we don't validate the timezone string against IANA
# because tzdata is huge and the kernel rejects bad timezones at use time).
COMMON_TIMEZONES: list[str] = [
    "Africa/Johannesburg",
    "Africa/Cairo",
    "Africa/Nairobi",
    "Africa/Lagos",
    "Africa/Accra",
    "Africa/Gaborone",
    "Africa/Windhoek",
    "Africa/Lusaka",
    "Africa/Harare",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "America/New_York",
    "America/Los_Angeles",
    "America/Chicago",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Australia/Sydney",
    "UTC",
]
