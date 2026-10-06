"""E.164 phone validation via app.utils.phone (owner directive 2026-10-06)."""

import pytest

from app.utils.phone import PhoneValidationError, is_e164, normalise_phone


class TestNormalisePhone:
    def test_already_e164_passthrough(self):
        assert normalise_phone("+263771234567") == "+263771234567"

    def test_strips_whitespace_and_formatting(self):
        assert normalise_phone("+263 (77) 123-4567") == "+263771234567"
        assert normalise_phone("+27 82 123 4567") == "+27821234567"

    def test_none_and_blank_return_none(self):
        assert normalise_phone(None) is None
        assert normalise_phone("") is None
        assert normalise_phone("   ") is None

    def test_local_format_without_prefix_raises(self):
        with pytest.raises(PhoneValidationError):
            normalise_phone("0771234567")

    def test_local_format_with_default_prefix_normalises(self):
        # Admin's tenant is in Zimbabwe (+263) — admin types the local
        # form, we fill the country code.
        assert normalise_phone("0771234567", default_prefix="263") == "+263771234567"
        assert normalise_phone("0771234567", default_prefix="+263") == "+263771234567"

    def test_local_format_without_leading_zero(self):
        # Some users type the subscriber number without the national
        # trunk 0 — fine, still produces a valid E.164.
        assert normalise_phone("771234567", default_prefix="263") == "+263771234567"

    def test_too_short_raises(self):
        with pytest.raises(PhoneValidationError):
            normalise_phone("+12345")

    def test_too_long_raises(self):
        with pytest.raises(PhoneValidationError):
            normalise_phone("+" + "1" * 16)

    def test_leading_zero_in_country_code_raises(self):
        with pytest.raises(PhoneValidationError):
            normalise_phone("+0771234567")


class TestIsE164:
    def test_true_for_valid(self):
        assert is_e164("+263771234567") is True

    def test_false_for_local(self):
        assert is_e164("0771234567") is False

    def test_false_for_blank(self):
        assert is_e164("") is False
        assert is_e164(None) is False
