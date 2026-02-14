"""User model with role-based access control."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, SoftDeleteMixin


class Role(str, Enum):
    """User roles with hierarchical permissions."""

    SUPER_ADMIN = "SUPER_ADMIN"  # Platform-wide admin (no tenant_id)
    SCHOOL_ADMIN = "SCHOOL_ADMIN"  # Tenant admin
    TEACHER = "TEACHER"  # Class-level access
    PARENT = "PARENT"  # Read-only access to own children


class User(Base, TimestampMixin, SoftDeleteMixin):
    """User account with role-based access."""

    __tablename__ = "users"
    __table_args__ = (
        # Email unique per tenant (NULL tenant_id for super admins handled separately)
        Index(
            "idx_users_email_tenant",
            "email",
            "tenant_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND tenant_id IS NOT NULL"),
        ),
        Index(
            "idx_users_email_super",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND tenant_id IS NULL"),
        ),
        Index(
            "idx_users_tenant_role",
            "tenant_id",
            "role",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,  # NULL for SUPER_ADMIN
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    avatar_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="en")
    whatsapp_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    whatsapp_opted_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="users", lazy="selectin")
    parent_students = relationship(
        "ParentStudent",
        back_populates="parent",
        foreign_keys="ParentStudent.parent_id",
        lazy="selectin",
    )
    teacher_classes = relationship(
        "TeacherClass",
        back_populates="teacher",
        foreign_keys="TeacherClass.teacher_id",
        lazy="selectin",
    )

    @property
    def full_name(self) -> str:
        """Get the user's full name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def is_super_admin(self) -> bool:
        """Check if user is a super admin."""
        return self.role == Role.SUPER_ADMIN.value

    @property
    def is_school_admin(self) -> bool:
        """Check if user is a school admin."""
        return self.role == Role.SCHOOL_ADMIN.value

    @property
    def is_teacher(self) -> bool:
        """Check if user is a teacher."""
        return self.role == Role.TEACHER.value

    @property
    def is_parent(self) -> bool:
        """Check if user is a parent."""
        return self.role == Role.PARENT.value

    @property
    def is_staff(self) -> bool:
        """Check if user is staff (admin or teacher)."""
        return self.role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value, Role.TEACHER.value)

    def has_role(self, *roles: Role | str) -> bool:
        """Check if user has any of the given roles."""
        role_values = [r.value if isinstance(r, Role) else r for r in roles]
        return self.role in role_values

    def can_access_student(self, student_id: uuid.UUID) -> bool:
        """Check if user can access a specific student."""
        if self.is_super_admin or self.is_school_admin:
            return True
        if self.is_teacher:
            # Teachers can access students in their classes
            # This should be checked via service layer with DB query
            return True
        if self.is_parent:
            # Parents can only access their own children
            return any(ps.student_id == student_id for ps in self.parent_students)
        return False
