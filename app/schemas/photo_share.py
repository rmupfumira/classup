"""Pydantic schemas for photo sharing."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class PhotoShareCreate(BaseModel):
    class_id: uuid.UUID
    caption: str | None = None
    file_ids: list[uuid.UUID]
    student_ids: list[uuid.UUID] = []

    @field_validator("file_ids")
    @classmethod
    def validate_file_ids(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(v) == 0:
            raise ValueError("At least one photo is required")
        return v


class PhotoFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_entity_id: uuid.UUID
    original_name: str
    thumbnail_url: str | None = None
    full_url: str | None = None


class TaggedStudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: uuid.UUID
    student_name: str


class PhotoShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    class_name: str | None = None
    caption: str | None = None
    sharer_name: str | None = None
    photo_count: int = 0
    photos: list[PhotoFileResponse] = []
    tagged_students: list[TaggedStudentResponse] = []
    created_at: datetime


class PhotoShareListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    class_name: str | None = None
    caption: str | None = None
    sharer_name: str | None = None
    photo_count: int = 0
    thumbnail_url: str | None = None
    tagged_student_names: list[str] = []
    created_at: datetime
