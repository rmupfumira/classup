"""Jurisdiction service — resolves the effective country, currency and
locale rules for a tenant.

Two layers of configuration:

  1. Platform defaults (super admin's Platform Settings page). Stored in
     ``system_settings.platform_defaults`` as ``default_country`` /
     ``default_currency`` / ``default_timezone`` / ``default_language``.
     Applied to every tenant that hasn't set its own overrides.

  2. Per-tenant override (super admin's Tenant Edit page). Stored in
     ``tenant.settings`` as ``country`` / ``billing_currency`` /
     ``timezone`` / ``language``. When present, wins over platform.

The country registry below is the source of truth for what a country
implies — currency, symbol, phone prefix, timezone default, VAT rate,
and which payment providers CAN work there. Payment integrations will
read from this to gate "for a ZA tenant, offer Yoco / PayFast; for a
ZW tenant, offer PayNow / EcoCash." Not wired up yet — this module
ships the data + resolver first.

Nothing here writes state. Read-only lookups on tenant/settings objects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Country registry — the source of truth for jurisdiction-specific rules.
#
# Adding a country here should be enough to make it selectable in the
# admin UI, seedable as a tenant default, and (once payment provider
# gating is wired) offerable as a payment option. Every field except
# `payment_providers` is used somewhere today; providers is scaffolding
# for the next milestone.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CountryProfile:
    """Everything ClassUp needs to know about a country."""

    code: str            # ISO 3166-1 alpha-2 (e.g. "ZA")
    name: str            # Display name
    currency: str        # ISO 4217 (e.g. "ZAR") — country's DEFAULT currency
    currency_symbol: str # For UI display (e.g. "R", "$")
    phone_prefix: str    # E.164 (e.g. "+27")
    timezone: str        # IANA (e.g. "Africa/Johannesburg")
    locale: str          # BCP 47 (e.g. "en-ZA")
    vat_rate: float      # Default VAT/GST rate (0.0-1.0). 0 = none.
    payment_providers: tuple[str, ...] = field(default_factory=tuple)
    """Payment provider slugs supported in this jurisdiction. Ordered
    by preference. Free-form strings so we don't couple this file to a
    concrete provider list — the payment_gateways module owns the
    provider registry proper."""


COUNTRY_REGISTRY: dict[str, CountryProfile] = {
    # Southern Africa — primary market
    "ZA": CountryProfile(
        code="ZA", name="South Africa",
        currency="ZAR", currency_symbol="R", phone_prefix="+27",
        timezone="Africa/Johannesburg", locale="en-ZA", vat_rate=0.15,
        payment_providers=("yoco", "payfast", "paygate", "ozow", "eft_manual"),
    ),
    "BW": CountryProfile(
        code="BW", name="Botswana",
        currency="BWP", currency_symbol="P", phone_prefix="+267",
        timezone="Africa/Gaborone", locale="en-BW", vat_rate=0.14,
        payment_providers=("eft_manual",),
    ),
    "NA": CountryProfile(
        code="NA", name="Namibia",
        currency="NAD", currency_symbol="N$", phone_prefix="+264",
        timezone="Africa/Windhoek", locale="en-NA", vat_rate=0.15,
        payment_providers=("eft_manual",),
    ),
    "ZM": CountryProfile(
        code="ZM", name="Zambia",
        currency="ZMW", currency_symbol="K", phone_prefix="+260",
        timezone="Africa/Lusaka", locale="en-ZM", vat_rate=0.16,
        payment_providers=("eft_manual",),
    ),
    "ZW": CountryProfile(
        code="ZW", name="Zimbabwe",
        # Zim officially uses ZiG (2024+). We list USD as the default
        # because most schools invoice in USD in practice; a tenant
        # can override to ZWL/ZIG at the settings level.
        currency="USD", currency_symbol="$", phone_prefix="+263",
        timezone="Africa/Harare", locale="en-ZW", vat_rate=0.15,
        payment_providers=("paynow", "ecocash", "eft_manual"),
    ),
    "MW": CountryProfile(
        code="MW", name="Malawi",
        currency="MWK", currency_symbol="MK", phone_prefix="+265",
        timezone="Africa/Blantyre", locale="en-MW", vat_rate=0.165,
        payment_providers=("eft_manual",),
    ),
    # East / West Africa
    "KE": CountryProfile(
        code="KE", name="Kenya",
        currency="KES", currency_symbol="KSh", phone_prefix="+254",
        timezone="Africa/Nairobi", locale="en-KE", vat_rate=0.16,
        payment_providers=("mpesa", "dpo", "eft_manual"),
    ),
    "NG": CountryProfile(
        code="NG", name="Nigeria",
        currency="NGN", currency_symbol="N",  # ISO NGN, Naira; symbol N without unicode to keep parser happy
        phone_prefix="+234",
        timezone="Africa/Lagos", locale="en-NG", vat_rate=0.075,
        payment_providers=("paystack", "flutterwave", "eft_manual"),
    ),
    "GH": CountryProfile(
        code="GH", name="Ghana",
        currency="GHS", currency_symbol="GHS", phone_prefix="+233",
        timezone="Africa/Accra", locale="en-GH", vat_rate=0.15,
        payment_providers=("paystack", "flutterwave", "eft_manual"),
    ),
    "UG": CountryProfile(
        code="UG", name="Uganda",
        currency="UGX", currency_symbol="USh", phone_prefix="+256",
        timezone="Africa/Kampala", locale="en-UG", vat_rate=0.18,
        payment_providers=("eft_manual",),
    ),
    "TZ": CountryProfile(
        code="TZ", name="Tanzania",
        currency="TZS", currency_symbol="TSh", phone_prefix="+255",
        timezone="Africa/Dar_es_Salaam", locale="en-TZ", vat_rate=0.18,
        payment_providers=("eft_manual",),
    ),
    # Rest of world — for schools with international parents
    "GB": CountryProfile(
        code="GB", name="United Kingdom",
        currency="GBP", currency_symbol="GBP", phone_prefix="+44",
        timezone="Europe/London", locale="en-GB", vat_rate=0.20,
        payment_providers=("stripe", "gocardless", "eft_manual"),
    ),
    "US": CountryProfile(
        code="US", name="United States",
        currency="USD", currency_symbol="$", phone_prefix="+1",
        timezone="America/New_York", locale="en-US", vat_rate=0.0,
        payment_providers=("stripe", "paypal", "eft_manual"),
    ),
    "AU": CountryProfile(
        code="AU", name="Australia",
        currency="AUD", currency_symbol="A$", phone_prefix="+61",
        timezone="Australia/Sydney", locale="en-AU", vat_rate=0.10,
        payment_providers=("stripe", "eft_manual"),
    ),
    "CA": CountryProfile(
        code="CA", name="Canada",
        currency="CAD", currency_symbol="C$", phone_prefix="+1",
        timezone="America/Toronto", locale="en-CA", vat_rate=0.05,
        payment_providers=("stripe", "eft_manual"),
    ),
    "IN": CountryProfile(
        code="IN", name="India",
        currency="INR", currency_symbol="INR", phone_prefix="+91",
        timezone="Asia/Kolkata", locale="en-IN", vat_rate=0.18,
        payment_providers=("razorpay", "stripe", "eft_manual"),
    ),
}


# Currency symbol lookup — used when we know the currency but not the
# country (a tenant on ZAR while physically in Zimbabwe, for example).
CURRENCY_SYMBOLS: dict[str, str] = {
    "ZAR": "R", "USD": "$", "EUR": "EUR", "GBP": "GBP",
    "KES": "KSh", "NGN": "N", "GHS": "GHS",
    "BWP": "P", "ZMW": "K", "MWK": "MK", "NAD": "N$",
    "AUD": "A$", "CAD": "C$", "INR": "INR",
    "UGX": "USh", "TZS": "TSh", "ZWL": "Z$",
    # Legacy — old records stored the SYMBOL as the currency. Map back.
    "R": "R", "$": "$",
}


@dataclass(frozen=True)
class Jurisdiction:
    """The effective jurisdiction for a tenant.

    Composed from (in priority order): tenant.settings overrides →
    country registry defaults → platform defaults → hardcoded ZA
    fallback. Never None — the fallback chain always produces one.
    """

    country: CountryProfile
    currency_code: str      # Effective — may differ from country.currency
    currency_symbol: str    # Symbol matching the effective currency
    timezone: str
    language: str
    """The tenant's UI language (BCP 47 short form: 'en', 'af').
    Distinct from country.locale which is a fuller ('en-ZA') tag."""

    @property
    def country_code(self) -> str:
        return self.country.code

    @property
    def country_name(self) -> str:
        return self.country.name

    @property
    def phone_prefix(self) -> str:
        return self.country.phone_prefix

    @property
    def vat_rate(self) -> float:
        return self.country.vat_rate

    @property
    def payment_providers(self) -> tuple[str, ...]:
        """Payment provider slugs available in this jurisdiction. Empty
        tuple = only manual EFT works (fall back to POP-based flow)."""
        return self.country.payment_providers


def _normalise_currency(value: Any) -> str | None:
    """Normalise a currency string to an ISO 4217 code where possible.

    Handles the legacy state where tenants have raw symbols in
    ``billing_currency`` ("R", "$"). Returns None if we can't make
    sense of the input at all — caller decides the fallback.
    """
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    if len(v) == 3 and v.isalpha():
        return v.upper()
    upper = v.upper()
    for iso, sym in CURRENCY_SYMBOLS.items():
        if len(iso) == 3 and (sym == v or sym.upper() == upper):
            return iso
    return None


def _fallback_country() -> CountryProfile:
    """Hardcoded last-resort country when neither tenant nor platform
    have provided one. ZA reflects ClassUp's primary market."""
    return COUNTRY_REGISTRY["ZA"]


def resolve_jurisdiction(
    tenant: Tenant | None,
    *,
    platform_defaults: dict[str, Any] | None = None,
) -> Jurisdiction:
    """Compose the effective jurisdiction from tenant settings +
    platform defaults + hardcoded fallback.

    Preferred entrypoint for anything already holding a Tenant. Callers
    that don't have platform_defaults handy can use
    :func:`get_jurisdiction_for_tenant` instead — it loads them from
    the DB.

    Never raises. Unknown/malformed values are logged and the fallback
    chain fills them in.
    """
    pd = platform_defaults or {}
    tenant_settings = (tenant.settings if tenant else {}) or {}

    # ── Country resolution ───────────────────────────────────────────
    tenant_country = str(tenant_settings.get("country") or "").upper().strip() or None
    platform_country = str(pd.get("default_country") or "").upper().strip() or None
    country_code = tenant_country or platform_country or "ZA"

    country = COUNTRY_REGISTRY.get(country_code)
    if country is None:
        logger.warning(
            "Unknown country code %r for tenant %s; falling back to ZA",
            country_code, getattr(tenant, "id", None),
        )
        country = _fallback_country()

    # ── Currency resolution ──────────────────────────────────────────
    # billing_currency may hold a legacy symbol from before ISO codes
    # were the norm; _normalise_currency handles that.
    tenant_currency = _normalise_currency(tenant_settings.get("billing_currency"))
    platform_currency = _normalise_currency(pd.get("default_currency"))
    currency_code = tenant_currency or platform_currency or country.currency
    currency_symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code)

    # ── Timezone / language — tenant → platform → country → default ──
    timezone = (
        str(tenant_settings.get("timezone") or "").strip()
        or str(pd.get("default_timezone") or "").strip()
        or country.timezone
    )
    language = (
        str(tenant_settings.get("language") or "").lower().strip()
        or str(pd.get("default_language") or "").lower().strip()
        or "en"
    )

    return Jurisdiction(
        country=country,
        currency_code=currency_code,
        currency_symbol=currency_symbol,
        timezone=timezone,
        language=language,
    )


async def get_jurisdiction_for_tenant(
    db: AsyncSession, tenant: Tenant | None,
) -> Jurisdiction:
    """Load platform defaults, then resolve for the tenant. Prefer this
    when you're already in an async context — one DB round-trip and you
    get the fully-resolved Jurisdiction."""
    from app.services import platform_service

    try:
        defaults = await platform_service.get_defaults(db)
        pd_dict = defaults.to_dict()
    except Exception:
        # Platform defaults absent or malformed — the resolver's own
        # fallback chain will cope. Unusual enough to log.
        logger.exception("Failed to load platform defaults; using hardcoded fallbacks")
        pd_dict = {}
    return resolve_jurisdiction(tenant, platform_defaults=pd_dict)


def format_amount(amount: Any, jurisdiction: Jurisdiction, *, decimals: int = 2) -> str:
    """Render an amount as ``R 1 234.56``. Space thousands separator
    (SA/ZW convention). Never raises — an unparseable amount comes back
    as ``{symbol} -``."""
    try:
        n = float(amount)
    except (TypeError, ValueError):
        return f"{jurisdiction.currency_symbol} -"
    sign = "-" if n < 0 else ""
    formatted = f"{abs(n):,.{decimals}f}".replace(",", " ")
    return f"{jurisdiction.currency_symbol} {sign}{formatted}"


# ---------------------------------------------------------------------------
# Reference data for admin UIs.
# ---------------------------------------------------------------------------


def list_countries() -> list[dict[str, str]]:
    """Country dropdown data — primary-market countries first, then
    alphabetical by name for the rest. Format matches what the
    existing platform_service dropdowns emit so admin templates can
    render either without special-casing."""
    priority = ["ZA", "BW", "NA", "ZM", "ZW", "MW"]
    priority_set = set(priority)
    rest = sorted(
        (c for c in COUNTRY_REGISTRY.values() if c.code not in priority_set),
        key=lambda c: c.name,
    )
    ordered = [COUNTRY_REGISTRY[code] for code in priority if code in COUNTRY_REGISTRY] + rest
    return [{"code": c.code, "name": c.name, "currency": c.currency} for c in ordered]


def list_currencies() -> list[dict[str, str]]:
    """Currency dropdown data — every unique currency across the country
    registry, plus a few standalone additions (EUR, ZWL). Sorted with
    ZAR / USD / EUR / GBP at the top."""
    seen: dict[str, dict[str, str]] = {}
    for country in COUNTRY_REGISTRY.values():
        if country.currency not in seen:
            seen[country.currency] = {
                "code": country.currency,
                "symbol": CURRENCY_SYMBOLS.get(country.currency, country.currency),
                "name": country.currency,
            }
    for extra in ("EUR", "ZWL"):
        if extra not in seen:
            seen[extra] = {
                "code": extra,
                "symbol": CURRENCY_SYMBOLS.get(extra, extra),
                "name": extra,
            }
    top = ["ZAR", "USD", "EUR", "GBP"]
    top_items = [seen[c] for c in top if c in seen]
    rest = sorted(
        (v for k, v in seen.items() if k not in top),
        key=lambda v: v["code"],
    )
    return top_items + rest
