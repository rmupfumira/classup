"""Tenant context management using contextvars.

This module provides thread-safe context variables for tracking the current
tenant, user, and role throughout a request lifecycle.
"""

import contextvars
import uuid
from typing import Any

from app.exceptions import TenantContextError, UserContextError

# Context variables for request-scoped data
_tenant_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "tenant_id", default=None
)
_current_user_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "current_user_id", default=None
)
_current_user_role: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_role", default=None
)
_current_user: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "current_user", default=None
)
_current_language: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_language", default="en"
)


# === Tenant Context ===

def get_tenant_id() -> uuid.UUID:
    """Get the current tenant ID.

    Returns:
        The current tenant's UUID

    Raises:
        TenantContextError: If tenant context is not set
    """
    tid = _tenant_id.get()
    if tid is None:
        raise TenantContextError("Tenant context is not set")
    return tid


def get_tenant_id_or_none() -> uuid.UUID | None:
    """Get the current tenant ID or None if not set.

    Use this when tenant context is optional (e.g., super admin endpoints).

    Returns:
        The current tenant's UUID or None
    """
    return _tenant_id.get()


def set_tenant_id(tid: uuid.UUID | None) -> None:
    """Set the current tenant ID.

    Args:
        tid: Tenant UUID to set (or None to clear)
    """
    _tenant_id.set(tid)


def clear_tenant_context() -> None:
    """Clear the tenant context."""
    _tenant_id.set(None)


# === User Context ===

def get_current_user_id() -> uuid.UUID:
    """Get the current user ID.

    Returns:
        The current user's UUID

    Raises:
        UserContextError: If user context is not set
    """
    uid = _current_user_id.get()
    if uid is None:
        raise UserContextError("User context is not set")
    return uid


def get_current_user_id_or_none() -> uuid.UUID | None:
    """Get the current user ID or None if not set.

    Returns:
        The current user's UUID or None
    """
    return _current_user_id.get()


def set_current_user_id(uid: uuid.UUID | None) -> None:
    """Set the current user ID.

    Args:
        uid: User UUID to set (or None to clear)
    """
    _current_user_id.set(uid)


# === Role Context ===

def get_current_user_role() -> str | None:
    """Get the current user's role.

    Returns:
        The current user's role string or None
    """
    return _current_user_role.get()


def set_current_user_role(role: str | None) -> None:
    """Set the current user's role.

    Args:
        role: Role string to set (or None to clear)
    """
    _current_user_role.set(role)


# === Full User Context ===

def get_current_user() -> Any:
    """Get the current user object.

    Returns:
        The current user object or None
    """
    return _current_user.get()


def set_current_user(user: Any) -> None:
    """Set the current user object.

    Args:
        user: User object to set (or None to clear)
    """
    _current_user.set(user)


# === Language Context ===

def get_current_language() -> str:
    """Get the current language code.

    Returns:
        The current language code (defaults to 'en')
    """
    return _current_language.get()


def set_current_language(lang: str) -> None:
    """Set the current language code.

    Args:
        lang: Language code to set
    """
    _current_language.set(lang)


# === Utility Functions ===

def clear_all_context() -> None:
    """Clear all context variables.

    Call this at the end of each request to prevent context leakage.
    """
    _tenant_id.set(None)
    _current_user_id.set(None)
    _current_user_role.set(None)
    _current_user.set(None)
    _current_language.set("en")


def is_super_admin() -> bool:
    """Check if the current user is a super admin.

    Returns:
        True if current user role is SUPER_ADMIN
    """
    role = get_current_user_role()
    return role == "SUPER_ADMIN"


def is_school_admin() -> bool:
    """Check if the current user is a school admin.

    Returns:
        True if current user role is SCHOOL_ADMIN
    """
    role = get_current_user_role()
    return role == "SCHOOL_ADMIN"


def is_teacher() -> bool:
    """Check if the current user is a teacher.

    Returns:
        True if current user role is TEACHER
    """
    role = get_current_user_role()
    return role == "TEACHER"


def is_parent() -> bool:
    """Check if the current user is a parent.

    Returns:
        True if current user role is PARENT
    """
    role = get_current_user_role()
    return role == "PARENT"


def is_staff() -> bool:
    """Check if the current user is staff (admin or teacher).

    Returns:
        True if current user is SUPER_ADMIN, SCHOOL_ADMIN, or TEACHER
    """
    role = get_current_user_role()
    return role in ("SUPER_ADMIN", "SCHOOL_ADMIN", "TEACHER")
