"""Bulk import job model for CSV imports."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid_extensions import uuid7

from app.models.base import Base, TimestampMixin


class ImportType(str, Enum):
    """Types of bulk imports supported."""

    STUDENTS = "STUDENTS"
    TEACHERS = "TEACHERS"
    PARENTS = "PARENTS"


class ImportStatus(str, Enum):
    """Status of an import job."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BulkImportJob(Base, TimestampMixin):
    """Tracks a bulk CSV import operation."""

    __tablename__ = "bulk_import_jobs"
    __table_args__ = (Index("idx_imports_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    import_type: Mapped[str] = mapped_column(String(30), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ImportStatus.PENDING.value,
    )
    total_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    column_mapping: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    created_by_user = relationship("User", lazy="selectin")

    @property
    def is_pending(self) -> bool:
        """Check if the import is pending."""
        return self.status == ImportStatus.PENDING.value

    @property
    def is_processing(self) -> bool:
        """Check if the import is being processed."""
        return self.status == ImportStatus.PROCESSING.value

    @property
    def is_completed(self) -> bool:
        """Check if the import has completed."""
        return self.status == ImportStatus.COMPLETED.value

    @property
    def is_failed(self) -> bool:
        """Check if the import failed."""
        return self.status == ImportStatus.FAILED.value

    @property
    def progress_percent(self) -> float:
        """Get the import progress as a percentage."""
        if not self.total_rows:
            return 0.0
        return (self.processed_rows / self.total_rows) * 100

    def add_error(self, row: int, field: str, value: str, message: str) -> None:
        """Add an error to the errors list."""
        errors = list(self.errors)
        errors.append({
            "row": row,
            "field": field,
            "value": value,
            "message": message,
        })
        self.errors = errors
        self.error_count = len(errors)

    def mark_processing(self, total_rows: int) -> None:
        """Mark the import as processing."""
        self.status = ImportStatus.PROCESSING.value
        self.total_rows = total_rows

    def mark_completed(self) -> None:
        """Mark the import as completed."""
        self.status = ImportStatus.COMPLETED.value
        self.completed_at = datetime.utcnow()

    def mark_failed(self, error_message: str | None = None) -> None:
        """Mark the import as failed."""
        self.status = ImportStatus.FAILED.value
        self.completed_at = datetime.utcnow()
        if error_message:
            self.add_error(0, "system", "", error_message)

    def increment_progress(self, success: bool = True) -> None:
        """Increment the processed row count."""
        self.processed_rows += 1
        if success:
            self.success_count += 1
