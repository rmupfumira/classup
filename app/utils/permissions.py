"""Role-based permission decorators and utilities."""

from functools import wraps
from typing import Callable

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import FeatureLockedException
from app.models.user import Role
from app.utils.tenant_context import (
    get_current_user_role,
    get_tenant_id_or_none,
)


def require_role(*allowed_roles: Role | str) -> Callable:
    """Decorator that enforces role-based access control.

    Usage:
        @router.post("/students")
        @require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
        async def create_student(...):
            ...

    Args:
        *allowed_roles: Roles that are allowed to access the endpoint

    Returns:
        Decorator function
    """
    # Convert Role enums to strings for comparison
    role_values = set()
    for role in allowed_roles:
        if isinstance(role, Role):
            role_values.add(role.value)
        else:
            role_values.add(role)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_role = get_current_user_role()

            # Super admins can access everything
            if current_role == Role.SUPER_ADMIN.value:
                return await func(*args, **kwargs)

            if current_role not in role_values:
                raise HTTPException(
                    status_code=403,
                    detail="You don't have permission to perform this action",
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_super_admin() -> Callable:
    """Decorator that requires SUPER_ADMIN role."""
    return require_role(Role.SUPER_ADMIN)


# ---------------------------------------------------------------------------
# Feature-flag enforcement
# ---------------------------------------------------------------------------

# Human-readable labels for features (used in error messages + /subscription banner)
FEATURE_LABELS: dict[str, str] = {
    "billing": "Billing",
    "photo_sharing": "Photo Sharing",
    "document_sharing": "Document Sharing",
    "timetable_management": "Timetable",
    "subject_management": "Subjects & Grading",
    "whatsapp_enabled": "WhatsApp Notifications",
}


def require_feature(feature: str) -> Callable:
    """FastAPI dependency that enforces a plan-gated feature flag.

    Usage:
        @router.get(
            "/fee-items",
            dependencies=[Depends(require_feature("billing"))],
        )
        async def list_fee_items(...): ...

    Behaviour:
    - SUPER_ADMIN always passes (can access any tenant for support).
    - Unauthenticated / missing tenant_id -> pass (other middleware handles).
    - Loads tenant, checks tenant.settings.features[feature].
    - Raises FeatureLockedException (402) if feature is disabled.
      The global exception handler turns this into a 402 JSON for API calls
      and a redirect to /subscription?locked=<feature> for web.
    """

    async def dependency(db: AsyncSession = Depends(get_db)) -> None:
        # Super admin bypasses all feature checks (support access)
        role = get_current_user_role()
        if role == Role.SUPER_ADMIN.value:
            return

        tenant_id = get_tenant_id_or_none()
        if not tenant_id:
            # No tenant context — other middleware/auth will reject elsewhere
            return

        # Lazy import to avoid circular: Tenant model pulls in other models
        from app.models import Tenant

        tenant = await db.get(Tenant, tenant_id)
        if not tenant:
            return

        features = (tenant.settings or {}).get("features", {})
        if not features.get(feature, False):
            label = FEATURE_LABELS.get(feature, feature.replace("_", " ").title())
            raise FeatureLockedException(feature, label)

    return dependency


def require_school_admin() -> Callable:
    """Decorator that requires SCHOOL_ADMIN or SUPER_ADMIN role."""
    return require_role(Role.SCHOOL_ADMIN)


def require_teacher() -> Callable:
    """Decorator that requires TEACHER, SCHOOL_ADMIN, or SUPER_ADMIN role."""
    return require_role(Role.TEACHER, Role.SCHOOL_ADMIN)


def require_staff() -> Callable:
    """Decorator that requires any staff role (TEACHER, SCHOOL_ADMIN, SUPER_ADMIN)."""
    return require_role(Role.TEACHER, Role.SCHOOL_ADMIN, Role.SUPER_ADMIN)


def require_authenticated() -> Callable:
    """Decorator that requires any authenticated user."""
    return require_role(Role.SUPER_ADMIN, Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)


class PermissionChecker:
    """Utility class for checking permissions programmatically."""

    def __init__(self, user_role: str | None):
        self.role = user_role

    @property
    def is_super_admin(self) -> bool:
        """Check if user is super admin."""
        return self.role == Role.SUPER_ADMIN.value

    @property
    def is_school_admin(self) -> bool:
        """Check if user is school admin."""
        return self.role == Role.SCHOOL_ADMIN.value

    @property
    def is_teacher(self) -> bool:
        """Check if user is teacher."""
        return self.role == Role.TEACHER.value

    @property
    def is_parent(self) -> bool:
        """Check if user is parent."""
        return self.role == Role.PARENT.value

    @property
    def is_staff(self) -> bool:
        """Check if user is staff (admin or teacher)."""
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.TEACHER.value,
        )

    def has_role(self, *roles: Role | str) -> bool:
        """Check if user has any of the given roles."""
        role_values = set()
        for role in roles:
            if isinstance(role, Role):
                role_values.add(role.value)
            else:
                role_values.add(role)
        return self.role in role_values

    def can_manage_users(self) -> bool:
        """Check if user can manage users."""
        return self.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value)

    def can_manage_classes(self) -> bool:
        """Check if user can manage classes."""
        return self.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value)

    def can_manage_students(self) -> bool:
        """Check if user can manage students."""
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.TEACHER.value,
        )

    def can_record_attendance(self) -> bool:
        """Check if user can record attendance."""
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.TEACHER.value,
        )

    def can_view_attendance(self) -> bool:
        """Check if user can view attendance records."""
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.TEACHER.value,
        )

    def can_create_reports(self) -> bool:
        """Check if user can create reports."""
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.TEACHER.value,
        )

    def can_send_announcements(self) -> bool:
        """Check if user can send school-wide announcements."""
        return self.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value)

    def can_send_class_announcements(self) -> bool:
        """Check if user can send class announcements."""
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.TEACHER.value,
        )

    def can_manage_settings(self) -> bool:
        """Check if user can manage tenant settings."""
        return self.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value)

    def can_manage_webhooks(self) -> bool:
        """Check if user can manage webhooks."""
        return self.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value)

    def can_bulk_import(self) -> bool:
        """Check if user can perform bulk imports."""
        return self.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value)

    def can_invite_parents(self) -> bool:
        """Check if user can invite parents."""
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.TEACHER.value,
        )

    def can_create_message(self) -> bool:
        """Check if user can create/compose new messages."""
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.TEACHER.value,
        )

    def can_reply_to_message(self) -> bool:
        """Check if user can reply to messages."""
        # All authenticated users can reply
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.TEACHER.value,
            Role.PARENT.value,
        )

    def can_manage_billing(self) -> bool:
        """Check if user can manage billing (fee items, invoices, payments)."""
        return self.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value)

    def can_view_billing(self) -> bool:
        """Check if user can view billing info (own children for parents)."""
        return self.role in (
            Role.SUPER_ADMIN.value,
            Role.SCHOOL_ADMIN.value,
            Role.PARENT.value,
        )


def get_permission_checker() -> PermissionChecker:
    """Get a PermissionChecker for the current user.

    Returns:
        PermissionChecker instance for current user's role
    """
    return PermissionChecker(get_current_user_role())
