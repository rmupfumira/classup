"""Document sharing model with scope levels (school/class/student)."""

import uuid
from enum import Enum

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScopedModel, TimestampMixin


class DocumentShareScope(str, Enum):
    SCHOOL = "SCHOOL"
    CLASS = "CLASS"
    STUDENT = "STUDENT"


class DocumentShare(TenantScopedModel):
    """A document share with scope-based visibility."""

    __tablename__ = "document_shares"
    __table_args__ = (
        Index(
            "idx_document_shares_tenant_scope",
            "tenant_id",
            "scope",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationships
    tenant = relationship("Tenant", lazy="selectin")
    school_class = relationship("SchoolClass", lazy="selectin")
    sharer = relationship("User", lazy="selectin")
    files = relationship(
        "DocumentShareFile",
        back_populates="document_share",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="DocumentShareFile.display_order",
    )
    tags = relationship(
        "DocumentShareTag",
        back_populates="document_share",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def file_count(self) -> int:
        return len(self.files) if self.files else 0

    @property
    def sharer_name(self) -> str | None:
        if self.sharer:
            return f"{self.sharer.first_name} {self.sharer.last_name}"
        return None

    @property
    def class_name(self) -> str | None:
        if self.school_class:
            return self.school_class.name
        return None

    @property
    def tagged_student_names(self) -> list[str]:
        if not self.tags:
            return []
        return [
            f"{tag.student.first_name} {tag.student.last_name}"
            for tag in self.tags
            if tag.student
        ]

    @property
    def primary_file_name(self) -> str | None:
        if self.files and self.files[0].file_entity:
            return self.files[0].file_entity.original_name
        return None


class DocumentShareFile(Base, TimestampMixin):
    """Join table linking document shares to file entities."""

    __tablename__ = "document_share_files"
    __table_args__ = ()

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_shares.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("file_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    document_share = relationship("DocumentShare", back_populates="files")
    file_entity = relationship("FileEntity", lazy="selectin")


class DocumentShareTag(Base, TimestampMixin):
    """Join table tagging students in a document share."""

    __tablename__ = "document_share_tags"
    __table_args__ = ()

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_shares.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationships
    document_share = relationship("DocumentShare", back_populates="tags")
    student = relationship("Student", lazy="selectin")
