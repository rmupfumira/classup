"""School events + parent RSVPs.

Data model per the design in
``alembic/versions/20260910_000002_add_school_events.py``:

  - Audience is COMPUTED from (scope, class_id, student_id) at send /
    query time. No materialised invitees table.
  - RSVPs are one-per-(event, user); the latest response overwrites via
    ON CONFLICT DO UPDATE at the service layer.
  - Soft-delete via ``deleted_at`` (matches every other tenant-scoped
    table in the codebase).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, String, Text, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class EventType(str, Enum):
    """Reason for the event. Drives icons + colour on the calendar view
    and lets tenants filter by type."""

    PARENT_MEETING = "PARENT_MEETING"
    PARENT_TEACHER_CONFERENCE = "PARENT_TEACHER_CONFERENCE"
    ASSEMBLY = "ASSEMBLY"
    OUTING = "OUTING"
    SPORTS = "SPORTS"
    OTHER = "OTHER"


class EventScope(str, Enum):
    """Who gets invited.

    - SCHOOL: every parent on the tenant.
    - CLASS: parents of every student in ``class_id``.
    - STUDENT: parents of ``student_id`` only.
    """

    SCHOOL = "SCHOOL"
    CLASS = "CLASS"
    STUDENT = "STUDENT"


class RsvpResponse(str, Enum):
    YES = "YES"
    NO = "NO"
    MAYBE = "MAYBE"


class SchoolEvent(Base, TimestampMixin):
    """An event scheduled by a school/teacher for parents."""

    __tablename__ = "school_events"
    __table_args__ = (
        Index(
            "idx_school_events_tenant_time",
            "tenant_id", "starts_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_school_events_class",
            "class_id",
            postgresql_where=text("class_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "idx_school_events_student",
            "student_id",
            postgresql_where=text("student_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default=EventType.PARENT_MEETING.value,
    )
    scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EventScope.SCHOOL.value,
    )
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="SET NULL"),
        nullable=True,
    )
    student_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="SET NULL"),
        nullable=True,
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """IANA timezone name (e.g. Africa/Johannesburg). Falls back to the
    tenant's configured timezone at render time if not set explicitly."""
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    rsvp_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        server_default=text("false"),
    )
    rsvp_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Relationships — lazy=selectin so listing events doesn't N+1 the
    # class/student lookup for display names.
    tenant = relationship("Tenant", lazy="selectin")
    school_class = relationship("SchoolClass", lazy="selectin")
    student = relationship("Student", lazy="selectin")
    creator = relationship("User", lazy="selectin", foreign_keys=[created_by])
    rsvps = relationship(
        "EventRsvp",
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None


class EventRsvp(Base):
    """A parent's response to an event. Unique per (event, user)."""

    __tablename__ = "event_rsvps"
    __table_args__ = (
        Index(
            "uq_event_rsvps_event_user",
            "event_id", "user_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    response: Mapped[str] = mapped_column(
        String(10), nullable=False, default=RsvpResponse.YES.value,
    )
    responded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    event = relationship("SchoolEvent", back_populates="rsvps", lazy="selectin")
    user = relationship("User", lazy="selectin")
