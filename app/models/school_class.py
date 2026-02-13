"""School class model and teacher-class relationships."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid_extensions import uuid7

from app.models.base import Base, TenantScopedModel, TimestampMixin


class SchoolClass(TenantScopedModel):
    """A class/group within a school."""

    __tablename__ = "school_classes"
    __table_args__ = (
        Index(
            "idx_classes_tenant",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_group: Mapped[str | None] = mapped_column(String(30), nullable=True)
    grade_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="school_classes", lazy="selectin")
    students = relationship(
        "Student",
        back_populates="school_class",
        lazy="selectin",
    )
    teacher_classes = relationship(
        "TeacherClass",
        back_populates="school_class",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    attendance_records = relationship(
        "AttendanceRecord",
        back_populates="school_class",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    daily_reports = relationship(
        "DailyReport",
        back_populates="school_class",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def student_count(self) -> int:
        """Get the number of students in this class."""
        return len([s for s in self.students if not s.is_deleted and s.is_active])

    @property
    def is_at_capacity(self) -> bool:
        """Check if the class is at capacity."""
        if self.capacity is None:
            return False
        return self.student_count >= self.capacity

    @property
    def primary_teacher(self) -> "TeacherClass | None":
        """Get the primary teacher for this class."""
        for tc in self.teacher_classes:
            if tc.is_primary:
                return tc
        return self.teacher_classes[0] if self.teacher_classes else None


class TeacherClass(Base, TimestampMixin):
    """Join table linking teachers to classes."""

    __tablename__ = "teacher_classes"
    __table_args__ = (
        Index("idx_teacher_classes_teacher", "teacher_id"),
        Index("idx_teacher_classes_class", "class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    # Relationships
    teacher = relationship(
        "User",
        back_populates="teacher_classes",
        foreign_keys=[teacher_id],
        lazy="selectin",
    )
    school_class = relationship(
        "SchoolClass",
        back_populates="teacher_classes",
        lazy="selectin",
    )
