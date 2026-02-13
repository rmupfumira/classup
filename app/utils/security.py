"""Security utilities for authentication and authorization."""

import uuid
from datetime import datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import settings

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt.

    Args:
        password: Plain text password to hash

    Returns:
        Bcrypt hash of the password
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a bcrypt hash.

    Args:
        plain_password: Plain text password to verify
        hashed_password: Bcrypt hash to verify against

    Returns:
        True if password matches, False otherwise
    """
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    role: str = "",
    name: str = "",
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token.

    Args:
        user_id: User's UUID
        tenant_id: Tenant's UUID (None for super admin)
        role: User's role
        name: User's display name
        expires_delta: Custom expiration time

    Returns:
        Encoded JWT token string
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)

    now = datetime.utcnow()
    expire = now + expires_delta

    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id) if tenant_id else None,
        "role": role,
        "name": name,
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),  # Unique token ID
        "type": "access",
    }

    return jwt.encode(
        payload,
        settings.effective_jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def create_refresh_token(
    user_id: uuid.UUID,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT refresh token.

    Args:
        user_id: User's UUID
        expires_delta: Custom expiration time

    Returns:
        Encoded JWT refresh token string
    """
    if expires_delta is None:
        expires_delta = timedelta(days=settings.jwt_refresh_token_expire_days)

    now = datetime.utcnow()
    expire = now + expires_delta

    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "type": "refresh",
    }

    return jwt.encode(
        payload,
        settings.effective_jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT token.

    Args:
        token: JWT token string to decode

    Returns:
        Token payload dictionary if valid, None if invalid/expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.effective_jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode an access token and verify it's an access token type.

    Args:
        token: JWT token string to decode

    Returns:
        Token payload dictionary if valid access token, None otherwise
    """
    payload = decode_token(token)
    if payload and payload.get("type") == "access":
        return payload
    return None


def decode_refresh_token(token: str) -> dict[str, Any] | None:
    """Decode a refresh token and verify it's a refresh token type.

    Args:
        token: JWT token string to decode

    Returns:
        Token payload dictionary if valid refresh token, None otherwise
    """
    payload = decode_token(token)
    if payload and payload.get("type") == "refresh":
        return payload
    return None


def get_user_id_from_token(token: str) -> uuid.UUID | None:
    """Extract user ID from a token.

    Args:
        token: JWT token string

    Returns:
        User UUID if token is valid, None otherwise
    """
    payload = decode_token(token)
    if payload and "sub" in payload:
        try:
            return uuid.UUID(payload["sub"])
        except (ValueError, TypeError):
            return None
    return None


def get_tenant_id_from_token(token: str) -> uuid.UUID | None:
    """Extract tenant ID from a token.

    Args:
        token: JWT token string

    Returns:
        Tenant UUID if present and valid, None otherwise
    """
    payload = decode_token(token)
    if payload and payload.get("tenant_id"):
        try:
            return uuid.UUID(payload["tenant_id"])
        except (ValueError, TypeError):
            return None
    return None
