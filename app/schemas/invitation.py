"""Parent invitation schemas."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class InvitationStatus(str, Enum):
    """Invitation status enum."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"


class InvitationCreate(BaseModel):
    """Schema for creating a parent invitation."""

    student_id: UUID
    email: EmailStr
    first_name: str = ""
    last_name: str = ""


class InvitationVerify(BaseModel):
    """Schema for verifying an invitation code."""

    code: str
    email: EmailStr


class InvitationResend(BaseModel):
    """Schema for resending an invitation."""

    invitation_id: UUID


class InvitationResponse(BaseModel):
    """Schema for invitation response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    student_id: UUID
    email: str
    first_name: str = ""
    last_name: str = ""
    invitation_code: str
    status: InvitationStatus
    created_by: UUID
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime

    # Joined data
    student_name: str | None = None
    created_by_name: str | None = None


class InvitationVerifyResponse(BaseModel):
    """Response when verifying an invitation."""

    valid: bool
    message: str
    student_name: str | None = None
    school_name: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class InvitationListResponse(BaseModel):
    """Response for listing invitations."""

    invitations: list[InvitationResponse]
    total: int
    page: int
    page_size: int
