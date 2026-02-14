"""Tenant-related Pydantic schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.tenant import EducationType


class TenantCreateRequest(BaseModel):
    """Schema for creating a new tenant."""

    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    phone: str | None = Field(None, max_length=50)
    address: str | None = None
    education_type: EducationType = EducationType.DAYCARE
    slug: str | None = Field(None, max_length=100, pattern=r"^[a-z0-9-]+$")


class TenantUpdateRequest(BaseModel):
    """Schema for updating a tenant."""

    name: str | None = Field(None, min_length=2, max_length=255)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    address: str | None = None
    is_active: bool | None = None
    settings: dict | None = None


class TenantSettingsUpdate(BaseModel):
    """Schema for updating tenant settings."""

    features: dict | None = None
    terminology: dict | None = None
    branding: dict | None = None
    timezone: str | None = None
    language: str | None = None


class TenantResponse(BaseModel):
    """Schema for tenant response."""

    id: uuid.UUID
    name: str
    slug: str
    email: str
    phone: str | None
    address: str | None
    logo_path: str | None
    education_type: str
    is_active: bool
    onboarding_completed: bool
    settings: dict
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TenantListItem(BaseModel):
    """Schema for tenant list item (summary)."""

    id: uuid.UUID
    name: str
    slug: str
    email: str
    education_type: str
    is_active: bool
    onboarding_completed: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TenantStatsResponse(BaseModel):
    """Schema for tenant statistics."""

    total_users: int
    users_by_role: dict
    total_teachers: int = 0
    total_students: int
    total_classes: int


class TenantAdminCreateRequest(BaseModel):
    """Schema for creating a tenant admin user."""

    email: EmailStr
    password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=50)


class TenantAdminResponse(BaseModel):
    """Schema for tenant admin response."""

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    phone: str | None
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PlatformStatsResponse(BaseModel):
    """Schema for platform-wide statistics."""

    total_tenants: int
    active_tenants: int
    tenants_by_type: dict
    total_users: int
    total_students: int
