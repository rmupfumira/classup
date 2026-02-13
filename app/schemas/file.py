"""File-related Pydantic schemas."""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FileCategory(str, Enum):
    """Categories of uploaded files."""

    PHOTO = "PHOTO"
    DOCUMENT = "DOCUMENT"
    AVATAR = "AVATAR"
    LOGO = "LOGO"


class FileUploadResponse(BaseModel):
    """Response after successful file upload."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    storage_path: str
    original_name: str
    content_type: str
    file_size: int
    file_category: str
    uploaded_by: uuid.UUID
    created_at: datetime

    # Computed fields
    file_size_human: str | None = None
    is_image: bool = False
    is_pdf: bool = False
    download_url: str | None = None


class FileEntityResponse(BaseModel):
    """Full file entity response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    storage_path: str
    original_name: str
    content_type: str
    file_size: int
    file_category: str
    uploaded_by: uuid.UUID
    created_at: datetime

    # Computed fields
    file_size_human: str | None = None
    is_image: bool = False
    is_pdf: bool = False
    download_url: str | None = None
    uploader_name: str | None = None


class FileListResponse(BaseModel):
    """Simplified file info for lists."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    content_type: str
    file_size: int
    file_category: str
    created_at: datetime
    is_image: bool = False
    thumbnail_url: str | None = None


class PresignedUrlResponse(BaseModel):
    """Response containing a presigned download URL."""

    url: str
    expires_in: int = Field(..., description="Expiry time in seconds")
    file_name: str
    content_type: str


class PhotoGalleryItem(BaseModel):
    """Photo item for gallery view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    content_type: str
    file_size: int
    created_at: datetime
    thumbnail_url: str | None = None
    full_url: str | None = None
    uploader_name: str | None = None

    # Related entity info
    class_name: str | None = None
    student_name: str | None = None


class DocumentListItem(BaseModel):
    """Document item for list view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    content_type: str
    file_size: int
    file_size_human: str | None = None
    created_at: datetime
    download_url: str | None = None
    uploader_name: str | None = None

    # Related entity info
    class_name: str | None = None
    category: str | None = None  # School-wide, Class, Student
