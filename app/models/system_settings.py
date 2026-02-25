"""SystemSettings model for platform-wide configuration (non-tenant-scoped)."""

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class SystemSettings(BaseModel):
    """Key-value store for platform-wide settings.

    Used for storing configuration that can be managed at runtime
    via the super admin UI (e.g., SMTP config).
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    value: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
