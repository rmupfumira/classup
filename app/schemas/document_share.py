"""Pydantic schemas for document sharing."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class DocumentShareCreate(BaseModel):
    scope: str
    class_id: uuid.UUID | None = None
    title: str
    description: str | None = None
    file_ids: list[uuid.UUID]
    student_ids: list[uuid.UUID] = []

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in ("SCHOOL", "CLASS", "STUDENT"):
            raise ValueError("Scope must be SCHOOL, CLASS, or STUDENT")
        return v

    @field_validator("file_ids")
    @classmethod
    def validate_file_ids(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(v) == 0:
            raise ValueError("At least one document file is required")
        return v

    @model_validator(mode="after")
    def validate_scope_requirements(self):
        if self.scope in ("CLASS", "STUDENT") and not self.class_id:
            raise ValueError("class_id is required for CLASS and STUDENT scope")
        if self.scope == "STUDENT" and not self.student_ids:
            raise ValueError("student_ids is required for STUDENT scope")
        return self


class DocumentFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_entity_id: uuid.UUID
    original_name: str
    content_type: str | None = None
    file_size: int | None = None
    view_url: str | None = None
    download_url: str | None = None


class TaggedStudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: uuid.UUID
    student_name: str


class DocumentShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope: str
    class_name: str | None = None
    title: str
    description: str | None = None
    sharer_name: str | None = None
    file_count: int = 0
    files: list[DocumentFileResponse] = []
    tagged_students: list[TaggedStudentResponse] = []
    created_at: datetime


class DocumentShareListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope: str
    class_name: str | None = None
    title: str
    description: str | None = None
    sharer_name: str | None = None
    file_count: int = 0
    primary_file_name: str | None = None
    tagged_student_names: list[str] = []
    created_at: datetime
