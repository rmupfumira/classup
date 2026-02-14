"""Report system models with template-driven architecture."""

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScopedModel, TimestampMixin


class ReportType(str, Enum):
    """Types of reports."""

    DAILY_ACTIVITY = "DAILY_ACTIVITY"  # Daily daycare report
    PROGRESS_REPORT = "PROGRESS_REPORT"  # Periodic progress update
    REPORT_CARD = "REPORT_CARD"  # Term/semester report card


class ReportFrequency(str, Enum):
    """Report generation frequency."""

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    TERMLY = "TERMLY"


class ReportStatus(str, Enum):
    """Report completion status."""

    DRAFT = "DRAFT"
    FINALIZED = "FINALIZED"


class ReportTemplate(TenantScopedModel):
    """Template defining the structure of a report type."""

    __tablename__ = "report_templates"
    __table_args__ = (
        Index(
            "idx_templates_tenant",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL AND is_active = true"),
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_type: Mapped[str] = mapped_column(String(30), nullable=False)
    frequency: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReportFrequency.DAILY.value,
    )
    applies_to_grade_level: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )  # Comma-separated: "TODDLER,PRESCHOOL"
    sections: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    grading_system_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grading_systems.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    grading_system = relationship("GradingSystem", lazy="selectin")
    daily_reports = relationship(
        "DailyReport",
        back_populates="template",
        lazy="selectin",
    )

    def applies_to_student(self, age_group: str | None, grade_level: str | None) -> bool:
        """Check if this template applies to a student's age/grade level."""
        if not self.applies_to_grade_level:
            # Universal template
            return True

        applicable = [
            level.strip().upper()
            for level in self.applies_to_grade_level.split(",")
        ]

        # Handle enum values - get the value if it's an enum
        age_group_str = age_group.value if hasattr(age_group, 'value') else age_group
        grade_level_str = grade_level.value if hasattr(grade_level, 'value') else grade_level

        if age_group_str and age_group_str.upper() in applicable:
            return True
        if grade_level_str and grade_level_str.upper() in applicable:
            return True

        return False


class DailyReport(TenantScopedModel):
    """A completed report for a student."""

    __tablename__ = "daily_reports"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "template_id",
            "report_date",
            name="uq_report_student_template_date",
        ),
        Index(
            "idx_reports_tenant_date",
            "tenant_id",
            "report_date",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_reports_student",
            "student_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_reports_class_date",
            "class_id",
            "report_date",
            postgresql_where=text("deleted_at IS NULL"),
        ),
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
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReportStatus.DRAFT.value,
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )

    # Relationships
    student = relationship("Student", back_populates="daily_reports", lazy="selectin")
    school_class = relationship("SchoolClass", back_populates="daily_reports", lazy="selectin")
    template = relationship("ReportTemplate", back_populates="daily_reports", lazy="selectin")
    created_by_user = relationship("User", lazy="selectin")

    @property
    def is_draft(self) -> bool:
        """Check if report is still a draft."""
        return self.status == ReportStatus.DRAFT.value

    @property
    def is_finalized(self) -> bool:
        """Check if report has been finalized."""
        return self.status == ReportStatus.FINALIZED.value

    def get_section_data(self, section_id: str) -> dict | None:
        """Get data for a specific section."""
        sections = self.report_data.get("sections", {})
        return sections.get(section_id)


def get_default_daycare_template_sections() -> list[dict]:
    """Get default sections for a daycare daily activity report."""
    return [
        {
            "id": "meals",
            "title": "Meals & Nutrition",
            "type": "CHECKLIST",
            "display_order": 1,
            "fields": [
                {
                    "id": "breakfast_amount",
                    "label": "Breakfast",
                    "type": "SELECT",
                    "options": ["All", "Most", "Some", "None", "N/A"],
                    "required": True,
                },
                {
                    "id": "breakfast_notes",
                    "label": "Breakfast Notes",
                    "type": "TEXT",
                    "required": False,
                },
                {
                    "id": "lunch_amount",
                    "label": "Lunch",
                    "type": "SELECT",
                    "options": ["All", "Most", "Some", "None", "N/A"],
                    "required": True,
                },
                {
                    "id": "lunch_notes",
                    "label": "Lunch Notes",
                    "type": "TEXT",
                    "required": False,
                },
                {
                    "id": "snack_amount",
                    "label": "Snack",
                    "type": "SELECT",
                    "options": ["All", "Most", "Some", "None", "N/A"],
                    "required": True,
                },
            ],
        },
        {
            "id": "nap",
            "title": "Rest Time",
            "type": "CHECKLIST",
            "display_order": 2,
            "fields": [
                {
                    "id": "nap_start",
                    "label": "Nap Start",
                    "type": "TIME",
                    "required": False,
                },
                {
                    "id": "nap_end",
                    "label": "Nap End",
                    "type": "TIME",
                    "required": False,
                },
                {
                    "id": "nap_quality",
                    "label": "Sleep Quality",
                    "type": "SELECT",
                    "options": ["Slept well", "Restless", "Did not sleep", "N/A"],
                    "required": False,
                },
            ],
        },
        {
            "id": "fluids",
            "title": "Fluids & Hydration",
            "type": "REPEATABLE_ENTRIES",
            "display_order": 3,
            "fields": [
                {"id": "time", "label": "Time", "type": "TIME", "required": True},
                {"id": "amount", "label": "Amount (ml)", "type": "NUMBER", "required": True},
                {
                    "id": "type",
                    "label": "Type",
                    "type": "SELECT",
                    "options": ["Water", "Milk", "Juice", "Formula"],
                    "required": True,
                },
            ],
        },
        {
            "id": "bathroom",
            "title": "Bathroom",
            "type": "REPEATABLE_ENTRIES",
            "display_order": 4,
            "fields": [
                {"id": "time", "label": "Time", "type": "TIME", "required": True},
                {
                    "id": "type",
                    "label": "Type",
                    "type": "SELECT",
                    "options": ["Wet", "BM", "Dry", "Potty"],
                    "required": True,
                },
                {"id": "notes", "label": "Notes", "type": "TEXT", "required": False},
            ],
        },
        {
            "id": "activities",
            "title": "Activities",
            "type": "CHECKLIST",
            "display_order": 5,
            "fields": [
                {
                    "id": "participated",
                    "label": "Activities participated in",
                    "type": "MULTISELECT",
                    "options": [
                        "Arts & Crafts",
                        "Music",
                        "Outdoor Play",
                        "Reading",
                        "Sensory Play",
                        "Free Play",
                        "Group Activity",
                    ],
                    "required": False,
                },
                {
                    "id": "mood",
                    "label": "Overall Mood",
                    "type": "SELECT",
                    "options": ["Happy", "Content", "Fussy", "Tired", "Unwell"],
                    "required": True,
                },
            ],
        },
        {
            "id": "teacher_notes",
            "title": "Teacher Notes",
            "type": "NARRATIVE",
            "display_order": 6,
            "fields": [
                {
                    "id": "notes",
                    "label": "Notes",
                    "type": "TEXTAREA",
                    "required": False,
                }
            ],
        },
    ]
