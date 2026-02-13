"""File storage model for tracking uploaded files."""

import uuid
from enum import Enum

from sqlalchemy import BigInteger, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid_extensions import uuid7

from app.models.base import TenantScopedModel


class FileCategory(str, Enum):
    """Categories of uploaded files."""

    PHOTO = "PHOTO"
    DOCUMENT = "DOCUMENT"
    AVATAR = "AVATAR"
    LOGO = "LOGO"


class FileEntity(TenantScopedModel):
    """Represents an uploaded file stored in R2."""

    __tablename__ = "file_entities"
    __table_args__ = (
        Index(
            "idx_files_tenant",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_category: Mapped[str] = mapped_column(String(20), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )

    # Relationships
    uploader = relationship("User", lazy="selectin")

    @property
    def is_image(self) -> bool:
        """Check if file is an image."""
        return self.content_type.startswith("image/")

    @property
    def is_pdf(self) -> bool:
        """Check if file is a PDF."""
        return self.content_type == "application/pdf"

    @property
    def file_extension(self) -> str:
        """Get the file extension."""
        if "." in self.original_name:
            return self.original_name.rsplit(".", 1)[-1].lower()
        return ""

    @property
    def file_size_human(self) -> str:
        """Get human-readable file size."""
        size = self.file_size
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
