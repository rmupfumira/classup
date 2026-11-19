"""Exam models — exam sets, per-subject papers (+ timetable), and
per-student results.

One Exam belongs to one class in one (academic_year, term). Creating
the exam auto-seeds an ExamSubject row per subject that class takes
(from ``class_subjects``), using the class subject's total_marks as
the default. Teachers mark each paper by writing ExamResults; on
finalisation, the marks copy into the end-of-term REPORT_CARD for
each student under the matching ACADEMIC_GRADES section.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ExamStatus:
    """Simple string constants for exam.status — the migration leaves
    the column un-typed so we can add states later without an enum
    migration."""

    DRAFT = "DRAFT"              # created, no timetable yet
    SCHEDULED = "SCHEDULED"       # at least one paper scheduled
    IN_PROGRESS = "IN_PROGRESS"   # some papers marked
    COMPLETED = "COMPLETED"       # every paper has results for every student
    FINALIZED = "FINALIZED"       # locked — results fed into report cards


class Exam(Base, TimestampMixin):
    """An exam sitting — one per (class, term, year).

    Multiple "exams" per term are allowed (e.g. Midterm + End of Term)
    as long as the `name` differs; uniqueness is on
    (tenant, class, year, term, name).
    """

    __tablename__ = "exams"
    __table_args__ = (
        Index("idx_exams_tenant_class", "tenant_id", "class_id"),
        Index("idx_exams_tenant_term", "tenant_id", "academic_year", "term"),
        UniqueConstraint(
            "tenant_id", "class_id", "academic_year", "term", "name",
            name="uq_exams_tenant_class_year_term_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(10), nullable=False)
    term: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT", server_default="DRAFT",
    )
    report_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    grading_system_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grading_systems.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Relationships
    school_class = relationship("SchoolClass", lazy="selectin")
    report_template = relationship("ReportTemplate", lazy="selectin")
    grading_system = relationship("GradingSystem", lazy="selectin")
    exam_subjects = relationship(
        "ExamSubject", back_populates="exam",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="ExamSubject.display_order",
    )


class ExamSubject(Base, TimestampMixin):
    """One subject paper within an exam. Carries the timetable
    (scheduled_at + room + invigilator) and the paper's mark total."""

    __tablename__ = "exam_subjects"
    __table_args__ = (
        Index("idx_exam_subjects_exam", "exam_id"),
        Index("idx_exam_subjects_scheduled", "tenant_id", "scheduled_at"),
        # Deliberately no UNIQUE on (exam_id, subject_id) — ZIMSEC English
        # has 2 papers, Combined Science has 3, etc. The migration
        # 20261119_000003 dropped the older constraint.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("exams.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    class_subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_subjects.id", ondelete="SET NULL"),
        nullable=True,
    )
    paper_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_marks: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_mark: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    room: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invigilator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    # Share of the subject's final mark. On a single-paper subject
    # this stays at 100. For ZIMSEC English (Paper 1 + Paper 2) both
    # rows sit at 50; for Combined Science (Paper 1 MC, Paper 2
    # Structured, Paper 3 Practical) typically 30/50/20. The service
    # validates sum-to-100 per subject at save time.
    weight_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100",
    )

    # Relationships
    exam = relationship("Exam", back_populates="exam_subjects", lazy="selectin")
    subject = relationship("Subject", lazy="selectin")
    invigilator = relationship("User", lazy="selectin", foreign_keys=[invigilator_id])
    results = relationship(
        "ExamResult", back_populates="exam_subject",
        cascade="all, delete-orphan", lazy="noload",
    )


class ExamResult(Base, TimestampMixin):
    """One student's score on one exam subject.

    marks_obtained is optional so the table can hold "absent" or
    "not yet marked" rows. percentage + grade_letter are computed on
    save from the ExamSubject's total_marks + the exam's grading
    system, so reports don't have to recompute later.
    """

    __tablename__ = "exam_results"
    __table_args__ = (
        Index("idx_exam_results_subject", "exam_subject_id"),
        Index("idx_exam_results_student", "student_id"),
        UniqueConstraint(
            "exam_subject_id", "student_id",
            name="uq_exam_results_subject_student",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    exam_subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("exam_subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )
    marks_obtained: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=6, scale=2), nullable=True,
    )
    percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=5, scale=2), nullable=True,
    )
    grade_letter: Mapped[str | None] = mapped_column(String(10), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_absent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    marked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    marked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Relationships
    exam_subject = relationship("ExamSubject", back_populates="results", lazy="selectin")
    student = relationship("Student", lazy="selectin")
    marker = relationship("User", lazy="selectin", foreign_keys=[marked_by])
