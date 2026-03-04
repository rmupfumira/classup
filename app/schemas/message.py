"""Pydantic schemas for messaging."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class MessageCreate(BaseModel):
    """Schema for creating a new message."""

    student_id: uuid.UUID
    recipient_id: uuid.UUID
    body: str
    subject: str

    @field_validator("body")
    @classmethod
    def validate_body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message body cannot be empty")
        return v

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Subject cannot be empty")
        if len(v) > 255:
            raise ValueError("Subject must be 255 characters or less")
        return v


class MessageReply(BaseModel):
    """Schema for replying to a conversation."""

    body: str

    @field_validator("body")
    @classmethod
    def validate_body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Reply body cannot be empty")
        return v


class MessageResponse(BaseModel):
    """Schema for a single message in a conversation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_id: uuid.UUID
    sender_name: str | None = None
    sender_role: str | None = None
    message_type: str
    subject: str | None = None
    body: str
    student_id: uuid.UUID | None = None
    student_name: str | None = None
    class_name: str | None = None
    parent_message_id: uuid.UUID | None = None
    status: str
    is_read: bool = False
    created_at: datetime
    updated_at: datetime


class ConversationSummary(BaseModel):
    """Summary of a conversation for the inbox view."""

    model_config = ConfigDict(from_attributes=True)

    thread_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    student_photo_path: str | None = None
    class_name: str | None = None
    other_user_id: uuid.UUID
    other_user_name: str
    other_user_role: str | None = None
    subject: str | None = None
    last_message_body: str
    last_message_at: datetime
    last_message_sender_id: uuid.UUID
    unread_count: int = 0


class UnreadCountResponse(BaseModel):
    """Response for unread message count."""

    count: int
