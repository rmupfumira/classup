"""Base SQLAlchemy models and mixins."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid_extensions import uuid7


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class TimestampMixin:
    """Mixin that adds created_at and updated_at timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.utcnow,
        nullable=False,
    )


class SoftDeleteMixin:
    """Mixin that adds soft delete support."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @property
    def is_deleted(self) -> bool:
        """Check if the record is soft-deleted."""
        return self.deleted_at is not None


class BaseModel(Base, TimestampMixin):
    """Abstract base model with ID and timestamps (for non-tenant-scoped entities)."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )


class TenantScopedModel(Base, TimestampMixin, SoftDeleteMixin):
    """Abstract base model for all tenant-scoped entities.

    Every tenant-scoped table MUST have a tenant_id foreign key.
    All queries against tenant-scoped tables MUST filter by tenant_id.
    """

    __abstract__ = True

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
