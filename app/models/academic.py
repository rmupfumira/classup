"""Academic models for subjects and grading systems."""

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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScopedModel, TimestampMixin


class Subject(TenantScopedModel):
    """A subject/course that can be taught at a school."""

    __tablename__ = "subjects"
    __table_args__ = (
        Index(
            "idx_subjects_tenant",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_subjects_tenant_code",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g., "ENG", "MATH"
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_total_marks: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g., "Core", "Elective", "Language"
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="subjects", lazy="selectin")
    class_subjects = relationship(
        "ClassSubject",
        back_populates="subject",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ClassSubject(Base, TimestampMixin):
    """Join table linking classes to subjects they teach."""

    __tablename__ = "class_subjects"
    __table_args__ = (
        Index("idx_class_subjects_class", "class_id"),
        Index("idx_class_subjects_subject", "subject_id"),
        Index(
            "idx_class_subjects_unique",
            "class_id",
            "subject_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    total_marks: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Override default if needed
    is_compulsory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    school_class = relationship(
        "SchoolClass",
        back_populates="class_subjects",
        lazy="selectin",
    )
    subject = relationship(
        "Subject",
        back_populates="class_subjects",
        lazy="selectin",
    )

    @property
    def effective_total_marks(self) -> int:
        """Get the total marks for this subject in this class."""
        return self.total_marks if self.total_marks is not None else self.subject.default_total_marks


class GradingSystem(TenantScopedModel):
    """A grading scale/system used for reports."""

    __tablename__ = "grading_systems"
    __table_args__ = (
        Index(
            "idx_grading_systems_tenant",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # JSONB array of grade definitions: [{min, max, grade, description, points}]
    # Example: [{"min": 80, "max": 100, "grade": "A", "description": "Excellent", "points": 4.0}]
    grades: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Relationships
    tenant = relationship("Tenant", back_populates="grading_systems", lazy="selectin")

    def get_grade_for_percentage(self, percentage: float) -> dict | None:
        """Get the grade info for a given percentage."""
        for grade_info in self.grades:
            if grade_info.get("min", 0) <= percentage <= grade_info.get("max", 100):
                return grade_info
        return None

    def get_grade_letter(self, percentage: float) -> str:
        """Get just the grade letter for a percentage."""
        grade_info = self.get_grade_for_percentage(percentage)
        return grade_info.get("grade", "-") if grade_info else "-"
