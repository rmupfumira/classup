"""Webhook schemas."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, HttpUrl


class WebhookEventType(str, Enum):
    """Supported webhook event types."""

    STUDENT_CREATED = "student.created"
    STUDENT_UPDATED = "student.updated"
    STUDENT_DELETED = "student.deleted"
    ATTENDANCE_MARKED = "attendance.marked"
    ATTENDANCE_BULK = "attendance.bulk"
    REPORT_CREATED = "report.created"
    REPORT_FINALIZED = "report.finalized"
    TEACHER_ADDED = "teacher.added"
    PARENT_REGISTERED = "parent.registered"
    CLASS_CREATED = "class.created"
    IMPORT_COMPLETED = "import.completed"


class WebhookEventStatus(str, Enum):
    """Webhook event delivery status."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class WebhookEndpointCreate(BaseModel):
    """Schema for creating a webhook endpoint."""

    url: HttpUrl
    events: list[WebhookEventType]
    is_active: bool = True


class WebhookEndpointUpdate(BaseModel):
    """Schema for updating a webhook endpoint."""

    url: HttpUrl | None = None
    events: list[WebhookEventType] | None = None
    is_active: bool | None = None


class WebhookEndpointResponse(BaseModel):
    """Schema for webhook endpoint response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WebhookEventResponse(BaseModel):
    """Schema for webhook event response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    endpoint_id: UUID
    event_type: str
    payload: dict
    status: WebhookEventStatus
    attempts: int
    last_attempt_at: datetime | None = None
    response_code: int | None = None
    response_body: str | None = None
    created_at: datetime


class WebhookTestRequest(BaseModel):
    """Schema for testing a webhook."""

    event_type: WebhookEventType = WebhookEventType.STUDENT_CREATED


class WebhookTestResponse(BaseModel):
    """Response from webhook test."""

    success: bool
    status_code: int | None = None
    response_body: str | None = None
    error: str | None = None
