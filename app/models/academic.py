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
    # Which curriculum pack this subject was seeded from (ZW_ZIMSEC,
    # ZA_CAPS, ZA_IEB, ZW_CAMBRIDGE, ZA_CAMBRIDGE, ...). NULL for
    # subjects created manually or seeded by legacy code paths. Used
    # by the class→grading-system inferrer so a class full of ZIMSEC
    # subjects gets the ZIMSEC grading scale by default.
    curriculum_pack_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )

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


class ClassSubjectTeacher(Base, TimestampMixin):
    """Links a teacher to a specific subject within a class.

    Only used when ``tenant.settings.subject_teacher_enabled`` is True
    — the secondary-school pattern where each subject has its own
    specialist (vs the primary-school pattern of one general class
    teacher who grades every subject).

    A teacher can:
    - Teach multiple subjects across multiple classes (many rows).
    - Simultaneously be a general class teacher (``teacher_classes``
      row with ``is_primary=True`` or false) for one class AND a
      subject teacher for subjects in other classes — the two tables
      coexist cleanly.

    Permission semantics (enforced by ``academic_service`` /
    ``report_service`` / ``message_service``):
    - Subject-teacher mode OFF → ignored entirely; the general class
      teacher continues to handle every subject (status quo).
    - Subject-teacher mode ON:
        * The teacher assigned here grades + comments on this
          specific subject for every student in that class.
        * They can message parents of those students, scoped to that
          subject.
        * They see only their own subject's grades on a student's
          report UNLESS they're also the general class teacher of
          that class (teacher_classes row), in which case they see
          everything.
    """

    __tablename__ = "class_subject_teachers"
    __table_args__ = (
        Index("idx_cst_class", "class_id"),
        Index("idx_cst_subject", "subject_id"),
        Index("idx_cst_teacher", "teacher_id"),
        Index(
            "idx_cst_unique",
            "class_id",
            "subject_id",
            "teacher_id",
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
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    # Relationships — lazy="selectin" so admin views that render a
    # subject with its assigned teachers don't N+1.
    school_class = relationship("SchoolClass", lazy="selectin")
    subject = relationship("Subject", lazy="selectin")
    teacher = relationship("User", lazy="selectin")


class StudentSubjectOverride(Base, TimestampMixin):
    """Per-student override of a class's subject.

    Normally every student in a class takes the class's full set of
    ``class_subjects``. In Combined-curriculum schools (ZIMSEC +
    Cambridge coexisting), individual students may follow a different
    syllabus for one or more subjects — e.g. the class takes
    ZIMSEC Mathematics but Alice takes Cambridge Mathematics instead.

    This table records those swaps:

    - ``class_subject_id`` → the row the student is NOT taking.
    - ``override_subject_id`` → the subject they take instead
      (must exist in ``subjects`` on the same tenant; need not be
      pre-assigned to any class via ``class_subjects``).

    Semantics (enforced by
    ``academic_service.get_effective_subjects_for_student``):
    - A student with no override rows takes the plain ``class_subjects``
      list (unchanged behaviour for every pre-feature tenant).
    - A student with overrides sees `class_subjects` MINUS the
      overridden rows PLUS the override subjects, each carrying its
      own ``curriculum_pack_code`` so reports render it with the
      right grading scale.

    Not currently used for attendance or messaging — those stay
    class-scoped. Added 2026-10-12 with task #158.
    """

    __tablename__ = "student_subject_overrides"
    __table_args__ = (
        Index("idx_sso_student", "student_id"),
        Index("idx_sso_class_subject", "class_subject_id"),
        Index(
            "idx_sso_unique",
            "student_id",
            "class_subject_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )
    class_subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    override_subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationships — selectin so the admin UI's per-student overrides
    # list doesn't N+1 when loading a page of students.
    student = relationship("Student", lazy="selectin")
    class_subject = relationship("ClassSubject", lazy="selectin")
    override_subject = relationship(
        "Subject", foreign_keys=[override_subject_id], lazy="selectin",
    )


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
