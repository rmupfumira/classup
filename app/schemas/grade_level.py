"""Pydantic schemas for GradeLevel entities."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GradeLevelBase(BaseModel):
    """Base schema for grade level data."""

    name: str = Field(..., min_length=1, max_length=100, description="Display name (e.g., 'Grade 1', 'Toddler')")
    code: str = Field(..., min_length=1, max_length=50, description="Internal code (e.g., 'GRADE_1', 'TODDLER')")
    description: str | None = None
    display_order: int = Field(default=0, ge=0)


class GradeLevelCreate(GradeLevelBase):
    """Schema for creating a grade level."""

    pass


class GradeLevelUpdate(BaseModel):
    """Schema for updating a grade level."""

    name: str | None = Field(None, min_length=1, max_length=100)
    code: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = None
    display_order: int | None = Field(None, ge=0)
    is_active: bool | None = None


class GradeLevelResponse(GradeLevelBase):
    """Schema for grade level response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


class GradeLevelListResponse(BaseModel):
    """Schema for grade level list item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    description: str | None
    display_order: int
    is_active: bool


class GradeLevelBasicInfo(BaseModel):
    """Basic grade level info for embedding in other responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
