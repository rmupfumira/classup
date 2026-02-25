"""Teacher invitation model for onboarding teachers."""

import secrets
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TeacherInvitation(Base, TimestampMixin):
    """Invitation for a teacher to join a school."""

    __tablename__ = "teacher_invitations"
    __table_args__ = (
        Index(
            "idx_teacher_invitations_code",
            "invitation_code",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "idx_teacher_invitations_email",
            "email",
            "tenant_id",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    invitation_code: Mapped[str] = mapped_column(
        String(8),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    tenant = relationship("Tenant", lazy="selectin")
    created_by_user = relationship("User", lazy="selectin")

    @property
    def is_expired(self) -> bool:
        """Check if the invitation has expired."""
        if self.status == "EXPIRED":
            return True
        return datetime.utcnow() > self.expires_at.replace(tzinfo=None)

    @property
    def is_pending(self) -> bool:
        """Check if the invitation is still pending."""
        return self.status == "PENDING" and not self.is_expired

    @property
    def is_accepted(self) -> bool:
        """Check if the invitation has been accepted."""
        return self.status == "ACCEPTED"

    def mark_expired(self) -> None:
        """Mark the invitation as expired."""
        self.status = "EXPIRED"

    def mark_accepted(self) -> None:
        """Mark the invitation as accepted."""
        self.status = "ACCEPTED"
        self.accepted_at = datetime.utcnow()
