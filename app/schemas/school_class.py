"""Pydantic schemas for SchoolClass entities."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SchoolClassBase(BaseModel):
    """Base schema for school class data."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    age_group: str | None = None
    grade_level: str | None = None
    capacity: int | None = Field(None, ge=1)


class SchoolClassCreate(SchoolClassBase):
    """Schema for creating a school class."""

    pass


class SchoolClassUpdate(BaseModel):
    """Schema for updating a school class."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    age_group: str | None = None
    grade_level: str | None = None
    capacity: int | None = Field(None, ge=1)
    is_active: bool | None = None


class SchoolClassResponse(SchoolClassBase):
    """Schema for school class response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Computed fields
    student_count: int | None = None
    teacher_count: int | None = None


class SchoolClassListResponse(BaseModel):
    """Schema for school class list item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    age_group: str | None
    grade_level: str | None
    capacity: int | None
    is_active: bool

    # Computed fields
    student_count: int | None = None
    teacher_count: int | None = None
    primary_teacher_name: str | None = None


class SchoolClassDetailResponse(SchoolClassResponse):
    """Detailed school class response with relationships."""

    students: list["StudentBasicInfo"] = Field(default_factory=list)
    teachers: list["TeacherInfo"] = Field(default_factory=list)


class StudentBasicInfo(BaseModel):
    """Basic student information for class detail."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    photo_path: str | None
    is_active: bool


class TeacherInfo(BaseModel):
    """Teacher information for class detail."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    is_primary: bool


class AssignTeacherRequest(BaseModel):
    """Request to assign a teacher to a class."""

    teacher_id: uuid.UUID
    is_primary: bool = False


class SetPrimaryClassRequest(BaseModel):
    """Request to set a class as teacher's primary."""

    class_id: uuid.UUID


# Update forward refs
SchoolClassDetailResponse.model_rebuild()
