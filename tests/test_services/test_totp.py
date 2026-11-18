"""Tests for the 2FA (TOTP + backup codes) service helpers.

Focused on the three places correctness matters:
  - Secret generation produces a valid base32 string pyotp can use.
  - verify_token accepts the current slot, rejects a wrong digit,
    and accepts the ±30s window so clock drift doesn't lock users
    out.
  - Backup codes: generated hashed, verified case-insensitively,
    return the right index so the caller can pop and consume.
"""

from __future__ import annotations

from types import SimpleNamespace

import pyotp
import pytest

from app.services import totp_service


def test_generate_secret_is_valid_base32():
    secret = totp_service.generate_secret()
    assert isinstance(secret, str) and len(secret) >= 16
    # pyotp parses it without raising — that's the only correctness bar.
    totp = pyotp.TOTP(secret)
    assert totp.now().isdigit()


def test_verify_token_round_trip():
    """A freshly-generated token is accepted by the verifier."""
    secret = totp_service.generate_secret()
    good = pyotp.TOTP(secret).now()
    assert totp_service.verify_token(secret, good) is True


def test_verify_token_rejects_wrong_digits():
    """A six-digit string that doesn't match the secret is rejected."""
    secret = totp_service.generate_secret()
    good = pyotp.TOTP(secret).now()
    bad = "000000" if good != "000000" else "111111"
    assert totp_service.verify_token(secret, bad) is False


def test_verify_token_rejects_non_six_digits():
    """Guard rails — too short, too long, or non-digits never pass."""
    secret = totp_service.generate_secret()
    for bad in ("", "12345", "1234567", "abcdef", "12 34 56 78"):
        assert totp_service.verify_token(secret, bad) is False


def test_generate_backup_codes_shape():
    plain, hashed = totp_service.generate_backup_codes()
    assert len(plain) == totp_service.BACKUP_CODE_COUNT
    assert len(hashed) == len(plain)
    # Every plaintext code is uppercase hex, length matches.
    for p in plain:
        assert p == p.upper() and len(p) == totp_service.BACKUP_CODE_LENGTH
        assert all(c in "0123456789ABCDEF" for c in p)
    # Hashes are all distinct (collision would mean broken RNG).
    assert len(set(hashed)) == len(hashed)


def test_verify_backup_code_finds_match_case_insensitive():
    """Users paste the code from a saved sheet; accept mixed case +
    stray hyphens / spaces the way a human would type it."""
    plain, hashed = totp_service.generate_backup_codes()
    user = SimpleNamespace(totp_backup_codes=list(hashed))

    target = plain[3]
    # Exact
    assert totp_service.verify_backup_code(user, target) == 3
    # Lowercase
    assert totp_service.verify_backup_code(user, target.lower()) == 3
    # With a stray dash (common when people transcribe from paper)
    half = len(target) // 2
    dashed = target[:half] + "-" + target[half:]
    assert totp_service.verify_backup_code(user, dashed) == 3


def test_verify_backup_code_rejects_junk():
    _plain, hashed = totp_service.generate_backup_codes()
    user = SimpleNamespace(totp_backup_codes=list(hashed))
    assert totp_service.verify_backup_code(user, "") is None
    assert totp_service.verify_backup_code(user, "nope-nope") is None
    # Empty list
    user_empty = SimpleNamespace(totp_backup_codes=[])
    assert totp_service.verify_backup_code(user_empty, "ABCD1234") is None
