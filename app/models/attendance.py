"""Attendance tracking model."""

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class AttendanceStatus(str, Enum):
    """Attendance status options."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    EXCUSED = "EXCUSED"


class AttendanceRecord(Base, TimestampMixin):
    """Daily attendance record for a student."""

    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("student_id", "date", name="uq_attendance_student_date"),
        Index("idx_attendance_tenant_date", "tenant_id", "date"),
        Index("idx_attendance_class_date", "class_id", "date"),
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
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AttendanceStatus.ABSENT.value,
    )
    check_in_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    check_out_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    recorded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    student = relationship("Student", back_populates="attendance_records", lazy="selectin")
    school_class = relationship("SchoolClass", back_populates="attendance_records", lazy="selectin")
    recorded_by_user = relationship("User", lazy="selectin")

    @property
    def is_present(self) -> bool:
        """Check if student was present (including late)."""
        return self.status in (AttendanceStatus.PRESENT.value, AttendanceStatus.LATE.value)

    @property
    def duration(self) -> float | None:
        """Calculate attendance duration in hours."""
        if not self.check_in_time or not self.check_out_time:
            return None
        delta = self.check_out_time - self.check_in_time
        return delta.total_seconds() / 3600
