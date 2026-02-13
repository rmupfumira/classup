"""User-related Pydantic schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    """Base user schema with common fields."""

    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=50)


class UserCreate(UserBase):
    """Schema for creating a new user (admin/teacher)."""

    password: str = Field(..., min_length=8)
    role: str = Field(..., pattern="^(SCHOOL_ADMIN|TEACHER)$")


class UserUpdate(BaseModel):
    """Schema for updating a user."""

    email: EmailStr | None = None
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=50)
    is_active: bool | None = None
    language: str | None = Field(None, max_length=5)


class UserResponse(BaseModel):
    """User response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    email: str
    first_name: str
    last_name: str
    phone: str | None
    role: str
    avatar_path: str | None
    is_active: bool
    language: str
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def full_name(self) -> str:
        """Get user's full name."""
        return f"{self.first_name} {self.last_name}"


class UserListItem(BaseModel):
    """Simplified user schema for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    role: str
    is_active: bool
    avatar_path: str | None

    @property
    def full_name(self) -> str:
        """Get user's full name."""
        return f"{self.first_name} {self.last_name}"


class TeacherSummary(BaseModel):
    """Teacher summary for class assignments."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    avatar_path: str | None
    is_primary: bool = False

    @property
    def full_name(self) -> str:
        """Get teacher's full name."""
        return f"{self.first_name} {self.last_name}"


class ParentSummary(BaseModel):
    """Parent summary for student profiles."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    phone: str | None
    relationship: str = "PARENT"
    is_primary: bool = False

    @property
    def full_name(self) -> str:
        """Get parent's full name."""
        return f"{self.first_name} {self.last_name}"
