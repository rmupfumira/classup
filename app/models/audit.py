"""Audit log model — records user actions across the platform."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class AuditLevel(str, Enum):
    """Aggressiveness of audit logging."""

    MINIMAL = "MINIMAL"      # Auth + 403/402 + super admin actions
    STANDARD = "STANDARD"    # + all write operations
    VERBOSE = "VERBOSE"      # + all GET requests


class AuditLog(Base):
    """A single audit event."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_created_at", "created_at"),
        Index("idx_audit_user", "user_id", "created_at"),
        Index("idx_audit_tenant", "tenant_id", "created_at"),
        Index("idx_audit_action", "action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # Tenant & user are nullable — super-admin and unauth events have neither
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Denormalised snapshot in case the user/tenant is deleted later
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    user_role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tenant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Action classification
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    # e.g. "auth.login", "students.create", "billing.invoice.sent", "admin.tenant.edit"

    resource_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Raw HTTP details for troubleshooting
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Free-form extra context (query params, diff summary, etc.)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    # Relationships
    user = relationship("User", lazy="noload", foreign_keys=[user_id])
    tenant = relationship("Tenant", lazy="noload")
