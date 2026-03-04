"""Pydantic schemas for announcements."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class AnnouncementCreate(BaseModel):
    """Schema for creating an announcement."""

    title: str
    body: str
    level: str  # SCHOOL or CLASS
    severity: str = "INFO"  # INFO, WARNING, URGENT, EMERGENCY
    class_id: uuid.UUID | None = None
    expires_at: datetime | None = None
    is_pinned: bool = False

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        if v not in ("SCHOOL", "CLASS"):
            raise ValueError("Level must be SCHOOL or CLASS")
        return v

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in ("INFO", "WARNING", "URGENT", "EMERGENCY"):
            raise ValueError("Severity must be INFO, WARNING, URGENT, or EMERGENCY")
        return v

    @model_validator(mode="after")
    def validate_class_id(self):
        if self.level == "CLASS" and self.class_id is None:
            raise ValueError("class_id is required for CLASS-level announcements")
        if self.level == "SCHOOL" and self.class_id is not None:
            raise ValueError("class_id must not be set for SCHOOL-level announcements")
        return self


class AnnouncementUpdate(BaseModel):
    """Schema for updating an announcement."""

    title: str | None = None
    body: str | None = None
    severity: str | None = None
    expires_at: datetime | None = None
    is_pinned: bool | None = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str | None) -> str | None:
        if v is not None and v not in ("INFO", "WARNING", "URGENT", "EMERGENCY"):
            raise ValueError("Severity must be INFO, WARNING, URGENT, or EMERGENCY")
        return v


class AnnouncementResponse(BaseModel):
    """Schema for announcement responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    body: str
    level: str
    severity: str
    class_id: uuid.UUID | None = None
    expires_at: datetime | None = None
    is_pinned: bool
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    creator_name: str | None = None
    class_name: str | None = None
    is_expired: bool = False
    is_active: bool = True
