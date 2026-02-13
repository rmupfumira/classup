"""Messaging system models."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid_extensions import uuid7

from app.models.base import Base, TenantScopedModel, TimestampMixin


class MessageType(str, Enum):
    """Types of messages with different scopes and purposes."""

    ANNOUNCEMENT = "ANNOUNCEMENT"  # School-wide from admin
    CLASS_ANNOUNCEMENT = "CLASS_ANNOUNCEMENT"  # Class-wide from teacher
    STUDENT_MESSAGE = "STUDENT_MESSAGE"  # About specific student
    REPLY = "REPLY"  # Reply in a thread
    CLASS_PHOTO = "CLASS_PHOTO"  # Photo shared with class
    STUDENT_PHOTO = "STUDENT_PHOTO"  # Photo for specific student
    CLASS_DOCUMENT = "CLASS_DOCUMENT"  # Document shared with class
    STUDENT_DOCUMENT = "STUDENT_DOCUMENT"  # Document for specific student
    SCHOOL_DOCUMENT = "SCHOOL_DOCUMENT"  # School-wide document


class MessageStatus(str, Enum):
    """Message delivery status."""

    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"


class Message(TenantScopedModel):
    """A message in the communication system."""

    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "idx_messages_tenant_type",
            "tenant_id",
            "message_type",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_messages_thread",
            "parent_message_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_messages_class",
            "class_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_messages_student",
            "student_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_messages_sender",
            "sender_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    message_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="SET NULL"),
        nullable=True,
    )
    student_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )  # Deprecated: use message_recipients
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MessageStatus.SENT.value,
    )

    # Relationships
    sender = relationship("User", lazy="selectin")
    school_class = relationship("SchoolClass", lazy="selectin")
    student = relationship("Student", lazy="selectin")
    parent_message = relationship(
        "Message",
        remote_side="Message.id",
        lazy="selectin",
    )
    recipients = relationship(
        "MessageRecipient",
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    attachments = relationship(
        "MessageAttachment",
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MessageAttachment.display_order",
    )

    @property
    def is_announcement(self) -> bool:
        """Check if this is an announcement-type message."""
        return self.message_type in (
            MessageType.ANNOUNCEMENT.value,
            MessageType.CLASS_ANNOUNCEMENT.value,
        )

    @property
    def has_attachments(self) -> bool:
        """Check if message has attachments."""
        return len(self.attachments) > 0

    @property
    def reply_count(self) -> int:
        """Get the number of replies (only available after query with count)."""
        return getattr(self, "_reply_count", 0)


class MessageRecipient(Base):
    """Tracks message recipients and read status."""

    __tablename__ = "message_recipients"
    __table_args__ = (
        Index(
            "idx_msg_recipients_user_unread",
            "user_id",
            "is_read",
            postgresql_where=text("is_read = false"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    message = relationship("Message", back_populates="recipients", lazy="selectin")
    user = relationship("User", lazy="selectin")


class MessageAttachment(Base, TimestampMixin):
    """Links messages to file attachments."""

    __tablename__ = "message_attachments"
    __table_args__ = (Index("idx_msg_attachments_message", "message_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("file_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    message = relationship("Message", back_populates="attachments", lazy="selectin")
    file_entity = relationship("FileEntity", lazy="selectin")
