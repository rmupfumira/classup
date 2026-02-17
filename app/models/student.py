"""Student model and related entities."""

import uuid
from datetime import date
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScopedModel, TimestampMixin


class Gender(str, Enum):
    """Gender options."""

    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


# DEPRECATED: Use GradeLevel model instead
class AgeGroup(str, Enum):
    """Age groups for daycare/early childhood.

    DEPRECATED: This enum is deprecated. Use the GradeLevel model instead.
    Grade levels are now managed as tenant-scoped entities and inherited from class.
    """

    INFANT = "INFANT"
    TODDLER = "TODDLER"
    PRESCHOOL = "PRESCHOOL"
    KINDERGARTEN = "KINDERGARTEN"
    GRADE_R = "GRADE_R"
    GRADE_1 = "GRADE_1"
    GRADE_2 = "GRADE_2"
    GRADE_3 = "GRADE_3"
    GRADE_4 = "GRADE_4"
    GRADE_5 = "GRADE_5"
    GRADE_6 = "GRADE_6"
    GRADE_7 = "GRADE_7"
    GRADE_8 = "GRADE_8"
    GRADE_9 = "GRADE_9"
    GRADE_10 = "GRADE_10"
    GRADE_11 = "GRADE_11"
    GRADE_12 = "GRADE_12"


class Student(TenantScopedModel):
    """Student entity with comprehensive profile data."""

    __tablename__ = "students"
    __table_args__ = (
        Index(
            "idx_students_tenant_class",
            "tenant_id",
            "class_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_students_tenant_age",
            "tenant_id",
            "age_group",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # DEPRECATED: Use grade level from class instead
    age_group: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # DEPRECATED: Use grade level from class instead
    grade_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="SET NULL"),
        nullable=True,
    )
    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    medical_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    allergies: Mapped[str | None] = mapped_column(Text, nullable=True)
    emergency_contacts: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    enrollment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="students", lazy="selectin")
    school_class = relationship("SchoolClass", back_populates="students", lazy="selectin")
    parent_students = relationship(
        "ParentStudent",
        back_populates="student",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    attendance_records = relationship(
        "AttendanceRecord",
        back_populates="student",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    daily_reports = relationship(
        "DailyReport",
        back_populates="student",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def full_name(self) -> str:
        """Get the student's full name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def age(self) -> int | None:
        """Calculate the student's age in years."""
        if not self.date_of_birth:
            return None
        today = date.today()
        age = today.year - self.date_of_birth.year
        if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
            age -= 1
        return age

    @property
    def primary_parent(self) -> "ParentStudent | None":
        """Get the primary parent relationship."""
        for ps in self.parent_students:
            if ps.is_primary:
                return ps
        return self.parent_students[0] if self.parent_students else None

    @property
    def effective_grade_level_id(self) -> uuid.UUID | None:
        """Get the grade level ID from the student's class.

        Students inherit their grade level from the class they are assigned to.
        Returns None if the student has no class or the class has no grade level.
        """
        if self.school_class and self.school_class.grade_level_id:
            return self.school_class.grade_level_id
        return None

    @property
    def effective_grade_level(self) -> "GradeLevel | None":
        """Get the grade level object from the student's class.

        Students inherit their grade level from the class they are assigned to.
        Returns None if the student has no class or the class has no grade level.
        """
        if self.school_class and hasattr(self.school_class, 'grade_level_rel'):
            return self.school_class.grade_level_rel
        return None

    @property
    def effective_grade_level_name(self) -> str | None:
        """Get the grade level name from the student's class."""
        grade_level = self.effective_grade_level
        return grade_level.name if grade_level else None

    @property
    def is_deleted(self) -> bool:
        """Check if the student is soft deleted."""
        return self.deleted_at is not None


class ParentStudent(Base, TimestampMixin):
    """Join table linking parents to students with relationship metadata."""

    __tablename__ = "parent_students"
    __table_args__ = (
        Index("idx_parent_students_parent", "parent_id"),
        Index("idx_parent_students_student", "student_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )
    relationship_type: Mapped[str] = mapped_column(
        "relationship",  # Keep DB column name as 'relationship'
        String(30),
        nullable=False,
        default="PARENT",
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relationships
    parent = relationship(
        "User",
        back_populates="parent_students",
        foreign_keys=[parent_id],
        lazy="selectin",
    )
    student = relationship(
        "Student",
        back_populates="parent_students",
        lazy="selectin",
    )
