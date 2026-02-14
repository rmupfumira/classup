"""Notification model for in-app notifications."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class NotificationType(str, Enum):
    """Types of notifications."""

    # Attendance
    ATTENDANCE_MARKED = "ATTENDANCE_MARKED"
    ATTENDANCE_LATE = "ATTENDANCE_LATE"

    # Reports
    REPORT_FINALIZED = "REPORT_FINALIZED"
    REPORT_READY = "REPORT_READY"

    # Files
    PHOTO_SHARED = "PHOTO_SHARED"
    DOCUMENT_SHARED = "DOCUMENT_SHARED"

    # User management
    INVITATION_SENT = "INVITATION_SENT"
    TEACHER_ADDED = "TEACHER_ADDED"
    STUDENT_ADDED = "STUDENT_ADDED"
    CLASS_CREATED = "CLASS_CREATED"

    # System
    SETTINGS_CHANGED = "SETTINGS_CHANGED"
    IMPORT_COMPLETED = "IMPORT_COMPLETED"
    WHATSAPP_MESSAGE = "WHATSAPP_MESSAGE"


class Notification(Base, TimestampMixin):
    """In-app notification for a user."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "idx_notifications_user_unread",
            "user_id",
            "is_read",
            postgresql_where=text("is_read = false"),
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
        nullable=True,  # NULL for super admin notifications
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user = relationship("User", lazy="selectin")

    def mark_read(self) -> None:
        """Mark the notification as read."""
        self.is_read = True
        self.read_at = datetime.utcnow()

    @property
    def reference_url(self) -> str | None:
        """Get the URL for the referenced entity."""
        if not self.reference_type or not self.reference_id:
            return None

        url_map = {
            "report": f"/reports/{self.reference_id}",
            "attendance": f"/attendance/{self.reference_id}",
            "student": f"/students/{self.reference_id}",
            "class": f"/classes/{self.reference_id}",
            "invitation": f"/invitations/{self.reference_id}",
        }

        return url_map.get(self.reference_type)
