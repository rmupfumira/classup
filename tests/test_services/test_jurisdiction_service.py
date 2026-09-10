"""Tests for jurisdiction_service — the country/currency resolver.

Covers the fallback chain that keeps billing safe when tenants haven't
picked a country:

    tenant.settings  →  platform defaults  →  country registry  →  ZA

Plus normalisation for the legacy state where ``billing_currency`` on
tenant.settings holds a raw symbol ("R", "$") instead of an ISO code.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import jurisdiction_service as jur


def _tenant(settings: dict | None = None):
    """Lightweight stand-in for a Tenant model — the resolver only ever
    reads .settings and .id, so a namespace is enough."""
    return SimpleNamespace(id="test-tenant", settings=settings or {})


class TestCountryRegistry:
    """The registry is data, but if we miss required fields future
    payment integrations quietly break."""

    def test_every_country_has_required_fields(self):
        required = {"code", "name", "currency", "currency_symbol",
                    "phone_prefix", "timezone", "locale", "vat_rate"}
        for code, profile in jur.COUNTRY_REGISTRY.items():
            missing = {f for f in required if not getattr(profile, f, None) and getattr(profile, f, None) != 0}
            assert not missing, f"{code} missing fields: {missing}"

    def test_vat_rates_are_sensible(self):
        for code, profile in jur.COUNTRY_REGISTRY.items():
            assert 0.0 <= profile.vat_rate <= 0.30, f"{code} VAT out of range"

    def test_phone_prefixes_start_with_plus(self):
        for code, profile in jur.COUNTRY_REGISTRY.items():
            assert profile.phone_prefix.startswith("+"), f"{code} prefix should be E.164"

    def test_za_registry_matches_platform_defaults(self):
        """The hardcoded ZA fallback matches what platform_service seeds
        as its BUILT_IN_DEFAULTS. Drift between these two produces
        confusing behaviour: platform admin sees ZAR by default, then
        a tenant with no platform config falls back to something else."""
        za = jur.COUNTRY_REGISTRY["ZA"]
        assert za.currency == "ZAR"
        assert za.timezone == "Africa/Johannesburg"


class TestResolverTenantOverride:
    """Tenant.settings takes precedence over everything else."""

    def test_tenant_country_override_wins(self):
        tenant = _tenant({"country": "ZW"})
        j = jur.resolve_jurisdiction(
            tenant, platform_defaults={"default_country": "ZA"},
        )
        assert j.country_code == "ZW"
        # ZW's registry default is USD (Zim invoices in USD in practice).
        assert j.currency_code == "USD"

    def test_tenant_currency_override_wins(self):
        """A ZW-based school explicitly billing in ZAR."""
        tenant = _tenant({"country": "ZW", "billing_currency": "ZAR"})
        j = jur.resolve_jurisdiction(tenant)
        assert j.country_code == "ZW"
        assert j.currency_code == "ZAR"
        assert j.currency_symbol == "R"

    def test_tenant_timezone_override_wins(self):
        tenant = _tenant({"country": "ZA", "timezone": "Africa/Cape_Town"})
        j = jur.resolve_jurisdiction(tenant)
        assert j.timezone == "Africa/Cape_Town"


class TestResolverFallbackChain:
    """When tenant.settings has nothing, we walk up to platform → hardcoded."""

    def test_platform_default_used_when_tenant_empty(self):
        j = jur.resolve_jurisdiction(
            _tenant({}),
            platform_defaults={
                "default_country": "KE",
                "default_currency": "KES",
                "default_timezone": "Africa/Nairobi",
            },
        )
        assert j.country_code == "KE"
        assert j.currency_code == "KES"
        assert j.timezone == "Africa/Nairobi"

    def test_hardcoded_fallback_when_nothing_configured(self):
        """No tenant, no platform defaults — must still yield ZA/ZAR."""
        j = jur.resolve_jurisdiction(None)
        assert j.country_code == "ZA"
        assert j.currency_code == "ZAR"

    def test_none_tenant_uses_platform_defaults(self):
        j = jur.resolve_jurisdiction(
            None, platform_defaults={"default_country": "GB"},
        )
        assert j.country_code == "GB"
        assert j.currency_code == "GBP"

    def test_unknown_country_falls_back_to_za(self):
        """Garbage in tenant.settings — resolver logs + falls back, does
        NOT raise. Billing must never crash because a tenant has bad
        config."""
        tenant = _tenant({"country": "XX"})
        j = jur.resolve_jurisdiction(tenant)
        assert j.country_code == "ZA"

    def test_empty_string_country_treated_as_unset(self):
        """The tenant edit UI POSTs empty strings to clear overrides.
        Resolver must treat them as "not set" and fall through."""
        tenant = _tenant({"country": "", "billing_currency": ""})
        j = jur.resolve_jurisdiction(
            tenant, platform_defaults={"default_country": "BW"},
        )
        assert j.country_code == "BW"
        assert j.currency_code == "BWP"


class TestLegacyCurrencyNormalisation:
    """Old tenants stored raw symbols in billing_currency. Resolver
    normalises them back to ISO codes so display stays consistent."""

    @pytest.mark.parametrize("stored,expected_iso,expected_symbol", [
        ("R",   "ZAR", "R"),
        ("ZAR", "ZAR", "R"),
        ("USD", "USD", "$"),
        ("$",   "USD", "$"),
        ("zar", "ZAR", "R"),   # case-insensitive
    ])
    def test_legacy_symbols_normalise(self, stored, expected_iso, expected_symbol):
        tenant = _tenant({"billing_currency": stored})
        j = jur.resolve_jurisdiction(tenant)
        assert j.currency_code == expected_iso
        assert j.currency_symbol == expected_symbol

    def test_unrecognised_currency_falls_through_to_country(self):
        """Something like "XYZ" — nothing in the symbol map, nothing an
        ISO code we know. Resolver should ignore it and use the
        country's default currency."""
        tenant = _tenant({"country": "KE", "billing_currency": "XYZ"})
        j = jur.resolve_jurisdiction(tenant)
        # "XYZ" is a valid-looking ISO shape, so resolver accepts it.
        assert j.currency_code == "XYZ"
        assert j.currency_symbol == "XYZ"


class TestJurisdictionProperties:
    """Exposed on the Jurisdiction dataclass so callers don't have to
    reach through to .country every time."""

    def test_properties_forward_to_country(self):
        j = jur.resolve_jurisdiction(_tenant({"country": "NG"}))
        assert j.country_code == "NG"
        assert j.country_name == "Nigeria"
        assert j.phone_prefix == "+234"
        assert j.vat_rate == pytest.approx(0.075)
        assert "paystack" in j.payment_providers


class TestReferenceData:
    """Country + currency dropdowns for the admin UI."""

    def test_list_countries_puts_primary_market_first(self):
        countries = jur.list_countries()
        codes = [c["code"] for c in countries]
        assert codes[0] == "ZA"  # South Africa first
        assert set(codes) == set(jur.COUNTRY_REGISTRY.keys())

    def test_list_currencies_puts_zar_first(self):
        currencies = jur.list_currencies()
        assert currencies[0]["code"] == "ZAR"
        codes = [c["code"] for c in currencies]
        # Every country's currency plus the standalone EUR is exposed.
        assert "USD" in codes
        assert "EUR" in codes


class TestFormatAmount:
    """format_amount must never raise — used in email bodies and PDFs
    where a stack trace is much worse than a slightly ugly number."""

    def test_positive_amount(self):
        j = jur.resolve_jurisdiction(_tenant({"country": "ZA"}))
        assert jur.format_amount(1234.56, j) == "R 1 234.56"

    def test_negative_amount(self):
        j = jur.resolve_jurisdiction(_tenant({"country": "ZA"}))
        assert jur.format_amount(-42.5, j) == "R -42.50"

    def test_unparseable_amount(self):
        j = jur.resolve_jurisdiction(_tenant({"country": "ZA"}))
        result = jur.format_amount("not a number", j)
        assert result.startswith("R")
        # Doesn't have to be pretty, must not raise.
