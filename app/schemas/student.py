"""Pydantic schemas for Student entities."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.student import AgeGroup, Gender


class EmergencyContact(BaseModel):
    """Emergency contact information."""

    name: str
    phone: str
    relationship: str = "Parent"


class StudentBase(BaseModel):
    """Base schema for student data."""

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    date_of_birth: date | None = None
    gender: str | None = None
    age_group: str | None = None  # DEPRECATED: Grade level is inherited from class
    grade_level: str | None = None  # DEPRECATED: Grade level is inherited from class
    class_id: uuid.UUID | None = None
    medical_info: str | None = None
    allergies: str | None = None
    emergency_contacts: list[EmergencyContact] = Field(default_factory=list)
    notes: str | None = None


class StudentCreate(StudentBase):
    """Schema for creating a student."""

    pass


class StudentUpdate(BaseModel):
    """Schema for updating a student."""

    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    date_of_birth: date | None = None
    gender: str | None = None
    age_group: str | None = None
    grade_level: str | None = None
    class_id: uuid.UUID | None = None
    medical_info: str | None = None
    allergies: str | None = None
    emergency_contacts: list[EmergencyContact] | None = None
    notes: str | None = None
    is_active: bool | None = None


class StudentResponse(StudentBase):
    """Schema for student response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    is_active: bool
    enrollment_date: date
    created_at: datetime
    updated_at: datetime

    # Computed fields
    full_name: str | None = None
    age: int | None = None
    class_name: str | None = None
    # Grade level inherited from class
    effective_grade_level_id: uuid.UUID | None = None
    effective_grade_level_name: str | None = None


class StudentListResponse(BaseModel):
    """Schema for student list item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    age_group: str | None  # DEPRECATED
    grade_level: str | None  # DEPRECATED
    class_id: uuid.UUID | None
    is_active: bool
    photo_path: str | None

    # Computed/joined fields
    full_name: str | None = None
    class_name: str | None = None
    # Grade level inherited from class
    effective_grade_level_id: uuid.UUID | None = None
    effective_grade_level_name: str | None = None


class StudentDetailResponse(StudentResponse):
    """Detailed student response with relationships."""

    parents: list["ParentInfo"] = Field(default_factory=list)
    attendance_summary: dict | None = None


class ParentInfo(BaseModel):
    """Basic parent information."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    phone: str | None
    relationship_type: str
    is_primary: bool


class LinkParentRequest(BaseModel):
    """Request to link a parent to a student."""

    parent_id: uuid.UUID
    relationship_type: str = "PARENT"
    is_primary: bool = False


# Update forward refs
StudentDetailResponse.model_rebuild()
