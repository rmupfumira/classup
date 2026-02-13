"""Message-related Pydantic schemas."""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MessageType(str, Enum):
    """Types of messages with different scopes and purposes."""

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


class MessageCreate(BaseModel):
    """Schema for creating a message."""

    message_type: MessageType
    subject: str | None = Field(None, max_length=255)
    body: str = Field(..., min_length=1)
    class_id: uuid.UUID | None = None
    student_id: uuid.UUID | None = None
    parent_message_id: uuid.UUID | None = None  # For replies
    attachment_ids: list[uuid.UUID] = []  # File entity IDs


class MessageReply(BaseModel):
    """Schema for replying to a message."""

    body: str = Field(..., min_length=1)
    attachment_ids: list[uuid.UUID] = []


class RecipientInfo(BaseModel):
    """Information about a message recipient."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    first_name: str
    last_name: str
    is_read: bool
    read_at: datetime | None = None


class AttachmentInfo(BaseModel):
    """Information about a message attachment."""

    id: uuid.UUID
    file_entity_id: uuid.UUID
    original_name: str
    content_type: str
    file_size: int
    display_order: int


class MessageResponse(BaseModel):
    """Schema for message response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    sender_id: uuid.UUID
    message_type: str
    subject: str | None
    body: str
    class_id: uuid.UUID | None
    student_id: uuid.UUID | None
    parent_message_id: uuid.UUID | None
    status: str
    created_at: datetime
    updated_at: datetime

    # Computed fields
    sender_name: str | None = None
    sender_avatar: str | None = None
    class_name: str | None = None
    student_name: str | None = None
    is_read: bool = False
    attachments: list[AttachmentInfo] = []
    reply_count: int = 0


class MessageListResponse(BaseModel):
    """Simplified message for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_id: uuid.UUID
    sender_name: str
    sender_avatar: str | None = None
    message_type: str
    subject: str | None
    body_preview: str  # Truncated body
    class_name: str | None = None
    student_name: str | None = None
    is_read: bool
    has_attachments: bool
    created_at: datetime


class MessageThreadResponse(BaseModel):
    """Full message thread with replies."""

    original_message: MessageResponse
    replies: list[MessageResponse]
    participants: list[RecipientInfo]


class UnreadCountResponse(BaseModel):
    """Unread message count."""

    messages: int
    announcements: int


class MarkReadRequest(BaseModel):
    """Request to mark messages as read."""

    message_ids: list[uuid.UUID]


class AnnouncementCreate(BaseModel):
    """Schema for creating an announcement."""

    subject: str = Field(..., min_length=1, max_length=255)
    body: str = Field(..., min_length=1)
    class_id: uuid.UUID | None = None  # None = school-wide


class StudentMessageCreate(BaseModel):
    """Schema for sending a message about a student."""

    student_id: uuid.UUID
    subject: str | None = Field(None, max_length=255)
    body: str = Field(..., min_length=1)
    attachment_ids: list[uuid.UUID] = []
