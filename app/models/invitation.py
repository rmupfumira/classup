"""Parent invitation model for onboarding parents."""

import secrets
import uuid
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class InvitationStatus(str, Enum):
    """Status of a parent invitation."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"


def generate_invitation_code() -> str:
    """Generate a unique 8-character invitation code."""
    return secrets.token_urlsafe(6)[:8].upper()


class ParentInvitation(Base, TimestampMixin):
    """Invitation for a parent to join and link to their child."""

    __tablename__ = "parent_invitations"
    __table_args__ = (
        Index(
            "idx_invitations_code",
            "invitation_code",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "idx_invitations_email",
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
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    invitation_code: Mapped[str] = mapped_column(
        String(8),
        unique=True,
        nullable=False,
        default=generate_invitation_code,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=InvitationStatus.PENDING.value,
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
    student = relationship("Student", lazy="selectin")
    created_by_user = relationship("User", lazy="selectin")

    @property
    def is_expired(self) -> bool:
        """Check if the invitation has expired."""
        if self.status == InvitationStatus.EXPIRED.value:
            return True
        return datetime.utcnow() > self.expires_at.replace(tzinfo=None)

    @property
    def is_pending(self) -> bool:
        """Check if the invitation is still pending."""
        return self.status == InvitationStatus.PENDING.value and not self.is_expired

    @property
    def is_accepted(self) -> bool:
        """Check if the invitation has been accepted."""
        return self.status == InvitationStatus.ACCEPTED.value

    def mark_expired(self) -> None:
        """Mark the invitation as expired."""
        self.status = InvitationStatus.EXPIRED.value

    def mark_accepted(self) -> None:
        """Mark the invitation as accepted."""
        self.status = InvitationStatus.ACCEPTED.value
        self.accepted_at = datetime.utcnow()
