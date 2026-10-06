"""Bulk import job schemas."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ImportType(str, Enum):
    """Types of bulk imports."""

    STUDENTS = "STUDENTS"
    TEACHERS = "TEACHERS"
    PARENTS = "PARENTS"


class ImportStatus(str, Enum):
    """Import job status."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ImportError(BaseModel):
    """An error from import processing."""

    row: int
    field: str | None = None
    value: str | None = None
    message: str


class ColumnMapping(BaseModel):
    """Mapping from CSV column to system field."""

    csv_column: str
    system_field: str | None = None  # None means skip this column


class ImportJobCreate(BaseModel):
    """Schema for creating an import job (after upload)."""

    import_type: ImportType


class ImportJobStart(BaseModel):
    """Schema for starting an import with column mapping."""

    column_mapping: dict[str, str | None]  # csv_column -> system_field or None to skip


class ImportJobResponse(BaseModel):
    """Schema for import job response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    import_type: ImportType
    file_name: str
    status: ImportStatus
    total_rows: int | None = None
    processed_rows: int = 0
    success_count: int = 0
    error_count: int = 0
    errors: list[ImportError] = []
    column_mapping: dict[str, str | None] = {}
    created_by: UUID
    completed_at: datetime | None = None
    created_at: datetime


class ImportPreviewResponse(BaseModel):
    """Response for CSV preview (headers + sample rows).

    ``sample_rows`` values are nullable because ``csv.DictReader``
    yields ``None`` for missing fields on ragged rows (shorter than
    the header) and ``list[str]`` under a ``None`` key for overrun
    rows. Previously typed as ``dict[str, str]``, which rejected
    those rows and bubbled a Pydantic ValidationError up to the
    generic 500 handler — showing the admin a bare "Something went
    wrong" (tester bug #19, 2026-10-06).
    """

    headers: list[str]
    sample_rows: list[dict[str, str | list[str] | None]]
    total_rows: int
    available_fields: dict[str, list[str]]  # import_type -> list of valid fields


class ImportFieldInfo(BaseModel):
    """Information about a system field for mapping."""

    name: str
    label: str
    required: bool = False
    description: str | None = None
