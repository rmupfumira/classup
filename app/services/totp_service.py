"""Two-factor auth (TOTP + backup codes).

Design:
  - Enrolment: POST /auth/2fa/setup returns a base32 secret + an
    otpauth:// provisioning URI + a PNG QR. User scans into
    Google Authenticator / Authy / 1Password. Secret is persisted
    but `totp_enabled` stays False until they verify a token.
  - Activation: POST /auth/2fa/verify with the current 6-digit token
    flips `totp_enabled` to True and returns a one-time list of
    plaintext backup codes (hashed on disk — the client sees them
    once).
  - Login: when `totp_enabled=True`, password-ok login returns
    {status: "mfa_required", mfa_token: "<signed, 5-min JWT>"}.
    Client POSTs /auth/login/mfa with mfa_token + 6-digit code.
    On success the normal access_token is issued.
  - Backup code: same endpoint, accepts an 8-char code instead.
    Consumed on use (removed from the user's list).
  - Disable: POST /auth/2fa/disable with current password + token
    clears the secret + flag + codes.

Intentional constraints:
  - PARENT role can turn it on too, same endpoints. Protects
    daycare parents who want the extra assurance.
  - No SMS fallback, no email OTP — TOTP app only. Simpler to
    audit, no carrier bills, no SIM-swap risk.
  - 30-second window with 1-step tolerance on either side (pyotp
    default), so clock drift doesn't lock users out.
"""

from __future__ import annotations

import base64
import logging
import secrets
from io import BytesIO
from typing import TYPE_CHECKING

import pyotp
import qrcode

from app.utils.security import hash_password, verify_password

if TYPE_CHECKING:
    from app.models.user import User

logger = logging.getLogger(__name__)


BACKUP_CODE_COUNT = 10
BACKUP_CODE_LENGTH = 8


def generate_secret() -> str:
    """New base32 secret. 20 bytes of entropy is the pyotp default."""
    return pyotp.random_base32()


def provisioning_uri(user: "User", secret: str, issuer: str) -> str:
    """otpauth:// URI an authenticator app can scan. The account label
    is the user's email, issuer is the app/tenant name."""
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer)


def qr_png_base64(uri: str) -> str:
    """Render ``uri`` as a QR PNG and return a base64 data-URL ready to
    stick straight into an <img src=>. No disk IO."""
    img = qrcode.make(uri)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def verify_token(secret: str, token: str) -> bool:
    """Accept the token as valid if the pyotp verify passes within the
    1-step drift window (±30s)."""
    if not secret or not token:
        return False
    token = str(token).strip().replace(" ", "")
    if len(token) != 6 or not token.isdigit():
        return False
    try:
        return pyotp.TOTP(secret).verify(token, valid_window=1)
    except Exception:
        logger.exception("TOTP verify failed internally")
        return False


def generate_backup_codes() -> tuple[list[str], list[str]]:
    """Return (plaintext_codes, hashed_codes). Plaintext is shown to
    the user once at setup — hashed is what we persist.

    Codes are 8-char base32 so they're easy to copy from a printed
    sheet. We accept them case-insensitively on verify."""
    plain: list[str] = []
    hashed: list[str] = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = secrets.token_hex(BACKUP_CODE_LENGTH // 2).upper()
        plain.append(raw)
        hashed.append(hash_password(raw))
    return plain, hashed


def verify_backup_code(user: "User", code: str) -> int | None:
    """Return the index of the matching hashed code or None. Caller
    pops the index from `user.totp_backup_codes` to consume it."""
    if not code:
        return None
    candidate = code.strip().upper().replace(" ", "").replace("-", "")
    if not candidate or len(candidate) > 32:
        return None
    for i, hashed in enumerate(user.totp_backup_codes or []):
        try:
            if verify_password(candidate, hashed):
                return i
        except Exception:
            continue
    return None
