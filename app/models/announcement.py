"""Announcement model for school-wide and class-level announcements."""

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScopedModel, TimestampMixin


class AnnouncementLevel(str, Enum):
    """Announcement visibility level."""

    SCHOOL = "SCHOOL"
    CLASS = "CLASS"


class AnnouncementSeverity(str, Enum):
    """Announcement severity/priority."""

    INFO = "INFO"
    WARNING = "WARNING"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"


# Severity ordering for sorting (higher = more important)
SEVERITY_ORDER = {
    AnnouncementSeverity.INFO: 0,
    AnnouncementSeverity.WARNING: 1,
    AnnouncementSeverity.URGENT: 2,
    AnnouncementSeverity.EMERGENCY: 3,
}


class Announcement(TenantScopedModel):
    """Announcement for school-wide or class-level communication."""

    __tablename__ = "announcements"
    __table_args__ = (
        Index(
            "idx_announcements_tenant_active",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_announcements_tenant_class",
            "tenant_id",
            "class_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_announcements_expires_at",
            "expires_at",
            postgresql_where=text("deleted_at IS NULL AND expires_at IS NOT NULL"),
        ),
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default=AnnouncementSeverity.INFO.value)
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationships
    tenant = relationship("Tenant", lazy="selectin")
    school_class = relationship("SchoolClass", lazy="selectin")
    creator = relationship("User", lazy="selectin")
    dismissals = relationship("AnnouncementDismissal", back_populates="announcement", cascade="all, delete-orphan")

    @property
    def is_expired(self) -> bool:
        """Check if the announcement has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_active(self) -> bool:
        """Check if the announcement is active (not expired, not deleted)."""
        return not self.is_expired and not self.is_deleted


class AnnouncementDismissal(Base, TimestampMixin):
    """Tracks which users have dismissed an announcement."""

    __tablename__ = "announcement_dismissals"
    __table_args__ = (
        UniqueConstraint("announcement_id", "user_id", name="uq_announcement_dismissal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    announcement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("announcements.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    # Relationships
    announcement = relationship("Announcement", back_populates="dismissals")
    user = relationship("User", lazy="selectin")
