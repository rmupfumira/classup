"""Message models for teacher-parent messaging."""

import uuid
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScopedModel, TimestampMixin


class MessageType(str, Enum):
    """Types of messages."""

    ANNOUNCEMENT = "ANNOUNCEMENT"
    CLASS_ANNOUNCEMENT = "CLASS_ANNOUNCEMENT"
    STUDENT_MESSAGE = "STUDENT_MESSAGE"
    REPLY = "REPLY"
    CLASS_PHOTO = "CLASS_PHOTO"
    STUDENT_PHOTO = "STUDENT_PHOTO"
    CLASS_DOCUMENT = "CLASS_DOCUMENT"
    STUDENT_DOCUMENT = "STUDENT_DOCUMENT"
    SCHOOL_DOCUMENT = "SCHOOL_DOCUMENT"


class MessageStatus(str, Enum):
    """Message delivery status."""

    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"


class Message(TenantScopedModel):
    """A message sent between users, scoped to a student."""

    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "idx_messages_tenant_student",
            "tenant_id",
            "student_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_messages_tenant_sender",
            "tenant_id",
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
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="SENT", nullable=False)

    # Relationships
    sender = relationship("User", foreign_keys=[sender_id], lazy="selectin")
    student = relationship("Student", lazy="selectin")
    school_class = relationship("SchoolClass", lazy="selectin")
    parent_message = relationship("Message", remote_side="Message.id", lazy="select")
    replies = relationship(
        "Message",
        back_populates="parent_message",
        lazy="select",
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
    )


class MessageRecipient(Base):
    """Tracks message delivery and read status per recipient."""

    __tablename__ = "message_recipients"
    __table_args__ = (
        Index("uq_message_recipient", "message_id", "user_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
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
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    message = relationship("Message", back_populates="recipients")
    user = relationship("User", lazy="selectin")


class MessageAttachment(Base, TimestampMixin):
    """Links file entities to messages."""

    __tablename__ = "message_attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
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
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    message = relationship("Message", back_populates="attachments")
    file_entity = relationship("FileEntity", lazy="selectin")
