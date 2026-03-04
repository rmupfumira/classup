"""Photo sharing model for class photo galleries."""

import uuid

from sqlalchemy import ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScopedModel, TimestampMixin


class PhotoShare(TenantScopedModel):
    """A photo share to a class, optionally tagging students."""

    __tablename__ = "photo_shares"
    __table_args__ = (
        Index(
            "idx_photo_shares_tenant_class",
            "tenant_id",
            "class_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="CASCADE"),
        nullable=False,
    )
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
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
        "PhotoShareFile",
        back_populates="photo_share",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PhotoShareFile.display_order",
    )
    tags = relationship(
        "PhotoShareTag",
        back_populates="photo_share",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def photo_count(self) -> int:
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


class PhotoShareFile(Base, TimestampMixin):
    """Join table linking photo shares to file entities."""

    __tablename__ = "photo_share_files"
    __table_args__ = ()

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    photo_share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("photo_shares.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("file_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    photo_share = relationship("PhotoShare", back_populates="files")
    file_entity = relationship("FileEntity", lazy="selectin")


class PhotoShareTag(Base, TimestampMixin):
    """Join table tagging students in a photo share."""

    __tablename__ = "photo_share_tags"
    __table_args__ = ()

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    photo_share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("photo_shares.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationships
    photo_share = relationship("PhotoShare", back_populates="tags")
    student = relationship("Student", lazy="selectin")
