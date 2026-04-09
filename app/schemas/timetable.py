"""Timetable Pydantic schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PeriodConfig(BaseModel):
    """A single period row in the school-day config."""

    index: int = Field(..., ge=1)
    label: str = Field(..., min_length=1, max_length=50)
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    is_break: bool = False


class TimetableConfigUpdate(BaseModel):
    """Request body to replace the tenant's day config."""

    days: list[str] = Field(..., min_length=1, max_length=7)
    periods: list[PeriodConfig] = Field(..., min_length=1, max_length=20)


class TimetableConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    days: list[str]
    periods: list[dict]
    updated_at: datetime


class TimetableCreate(BaseModel):
    class_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)


class TimetableEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    day: str
    period_index: int
    subject_id: uuid.UUID
    subject_name: str | None = None
    teacher_id: uuid.UUID | None = None
    teacher_name: str | None = None
    has_conflict: bool = False


class TimetableResponse(BaseModel):
    id: uuid.UUID
    class_id: uuid.UUID
    class_name: str | None = None
    name: str
    is_active: bool
    entries: list[TimetableEntryResponse] = []
    created_at: datetime
    updated_at: datetime


class TimetableListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    class_id: uuid.UUID
    class_name: str | None = None
    name: str
    is_active: bool
    entry_count: int = 0
    created_at: datetime


class TimetableEntryUpsert(BaseModel):
    """Upsert a single cell in the grid."""

    day: str = Field(..., pattern=r"^(MON|TUE|WED|THU|FRI|SAT|SUN)$")
    period_index: int = Field(..., ge=1)
    subject_id: uuid.UUID
    teacher_id: uuid.UUID | None = None


class GenerateDraftRequest(BaseModel):
    """Request payload for auto-generating a draft timetable."""

    weekly_hours: dict[str, int] | None = None  # subject_id (str) -> periods per week


class GenerateDraftResponse(BaseModel):
    entries_created: int
    warnings: list[str] = []
