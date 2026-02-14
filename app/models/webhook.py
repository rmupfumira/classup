"""Webhook system models for third-party integrations."""

import secrets
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Boolean,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class WebhookEventType(str, Enum):
    """Supported webhook event types."""

    # Students
    STUDENT_CREATED = "student.created"
    STUDENT_UPDATED = "student.updated"
    STUDENT_DELETED = "student.deleted"

    # Attendance
    ATTENDANCE_MARKED = "attendance.marked"
    ATTENDANCE_BULK = "attendance.bulk"

    # Reports
    REPORT_CREATED = "report.created"
    REPORT_FINALIZED = "report.finalized"

    # Users
    TEACHER_ADDED = "teacher.added"
    PARENT_REGISTERED = "parent.registered"

    # Classes
    CLASS_CREATED = "class.created"

    # Import
    IMPORT_COMPLETED = "import.completed"


class WebhookEventStatus(str, Enum):
    """Status of a webhook delivery attempt."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


def generate_webhook_secret() -> str:
    """Generate a secure webhook signing secret."""
    return secrets.token_urlsafe(32)


class WebhookEndpoint(Base, TimestampMixin):
    """A registered webhook endpoint for a tenant."""

    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        Index(
            "idx_webhooks_tenant",
            "tenant_id",
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default=generate_webhook_secret,
    )
    events: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    webhook_events = relationship(
        "WebhookEvent",
        back_populates="endpoint",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def subscribes_to(self, event_type: str) -> bool:
        """Check if this endpoint subscribes to the given event type."""
        return event_type in self.events or "*" in self.events


class WebhookEvent(Base):
    """Record of a webhook delivery attempt."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index(
            "idx_webhook_events_status",
            "status",
            postgresql_where=text("status IN ('PENDING', 'FAILED')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=WebhookEventStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    # Relationships
    endpoint = relationship("WebhookEndpoint", back_populates="webhook_events", lazy="selectin")

    def record_attempt(
        self, success: bool, response_code: int | None = None, response_body: str | None = None
    ) -> None:
        """Record a delivery attempt."""
        self.attempts += 1
        self.last_attempt_at = datetime.utcnow()
        self.response_code = response_code
        self.response_body = response_body[:1000] if response_body else None

        if success:
            self.status = WebhookEventStatus.DELIVERED.value
        elif self.attempts >= 3:
            self.status = WebhookEventStatus.FAILED.value

    @property
    def can_retry(self) -> bool:
        """Check if this event can be retried."""
        return (
            self.status in (WebhookEventStatus.PENDING.value, WebhookEventStatus.FAILED.value)
            and self.attempts < 3
        )
