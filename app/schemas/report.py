"""Report system Pydantic schemas."""

import uuid
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ReportType(str, Enum):
    """Types of reports."""

    DAILY_ACTIVITY = "DAILY_ACTIVITY"
    PROGRESS_REPORT = "PROGRESS_REPORT"
    REPORT_CARD = "REPORT_CARD"


class ReportFrequency(str, Enum):
    """Report generation frequency."""

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    TERMLY = "TERMLY"


class ReportStatus(str, Enum):
    """Report completion status."""

    DRAFT = "DRAFT"
    FINALIZED = "FINALIZED"


# ============== Template Field Schemas ==============


class TemplateField(BaseModel):
    """A field within a report section."""

    id: str
    label: str
    type: str  # SELECT, MULTISELECT, TEXT, TEXTAREA, TIME, NUMBER, DATE
    options: list[str] | None = None  # For SELECT/MULTISELECT types
    required: bool = False
    placeholder: str | None = None
    min_value: float | None = None  # For NUMBER type
    max_value: float | None = None


class TemplateSection(BaseModel):
    """A section within a report template."""

    id: str
    title: str
    type: str  # CHECKLIST, REPEATABLE_ENTRIES, NARRATIVE
    display_order: int
    fields: list[TemplateField]
    description: str | None = None


# ============== Template Schemas ==============


class ReportTemplateCreate(BaseModel):
    """Schema for creating a report template."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    report_type: ReportType
    frequency: ReportFrequency = ReportFrequency.DAILY
    applies_to_grade_level: str | None = None  # Comma-separated
    sections: list[TemplateSection] = []
    display_order: int = 0
    is_active: bool = True


class ReportTemplateUpdate(BaseModel):
    """Schema for updating a report template."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    report_type: ReportType | None = None
    frequency: ReportFrequency | None = None
    applies_to_grade_level: str | None = None
    sections: list[TemplateSection] | None = None
    display_order: int | None = None
    is_active: bool | None = None


class ReportTemplateResponse(BaseModel):
    """Schema for report template response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    report_type: str
    frequency: str
    applies_to_grade_level: str | None
    sections: list[dict]
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ReportTemplateListResponse(BaseModel):
    """Schema for template list item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    report_type: str
    frequency: str
    applies_to_grade_level: str | None
    is_active: bool
    section_count: int = 0


# ============== Report Schemas ==============


class ReportCreate(BaseModel):
    """Schema for creating a daily report."""

    student_id: uuid.UUID
    class_id: uuid.UUID
    template_id: uuid.UUID
    report_date: date
    report_data: dict = Field(default_factory=dict)


class ReportUpdate(BaseModel):
    """Schema for updating a report (only draft reports)."""

    report_data: dict


class ReportFinalize(BaseModel):
    """Schema for finalizing a report."""

    notify_parents: bool = True


class StudentSummary(BaseModel):
    """Brief student info for report response."""

    id: uuid.UUID
    first_name: str
    last_name: str
    photo_path: str | None = None


class ClassSummary(BaseModel):
    """Brief class info for report response."""

    id: uuid.UUID
    name: str


class TemplateSummary(BaseModel):
    """Brief template info for report response."""

    id: uuid.UUID
    name: str
    report_type: str


class UserSummary(BaseModel):
    """Brief user info for report response."""

    id: uuid.UUID
    first_name: str
    last_name: str


class ReportResponse(BaseModel):
    """Schema for report response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    student_id: uuid.UUID
    class_id: uuid.UUID
    template_id: uuid.UUID
    report_date: date
    report_data: dict
    status: str
    finalized_at: datetime | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    # Nested objects
    student: StudentSummary | None = None
    school_class: ClassSummary | None = None
    template: TemplateSummary | None = None
    created_by_user: UserSummary | None = None


class ReportListResponse(BaseModel):
    """Schema for report list item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    class_id: uuid.UUID
    class_name: str
    template_id: uuid.UUID
    template_name: str
    report_type: str
    report_date: date
    status: str
    finalized_at: datetime | None
    created_at: datetime


# ============== Report Data Section Schemas ==============


class SectionData(BaseModel):
    """Data for a single section in a report."""

    section_id: str
    data: dict


class RepeatableEntry(BaseModel):
    """A single entry in a repeatable section."""

    data: dict


class RepeatableSectionData(BaseModel):
    """Data for a repeatable section."""

    section_id: str
    entries: list[RepeatableEntry]
