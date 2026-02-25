"""Super Admin API routes for tenant management and platform settings."""

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.system_settings import SystemSettings
from app.models.tenant import EducationType
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.tenant import (
    PlatformStatsResponse,
    TenantAdminCreateRequest,
    TenantAdminResponse,
    TenantCreateRequest,
    TenantListItem,
    TenantResponse,
    TenantStatsResponse,
    TenantUpdateRequest,
)
from app.services.email_service import EMAIL_CONFIG_KEY, get_email_service
from app.services.tenant_service import get_tenant_service
from app.utils.permissions import require_super_admin
from app.utils.tenant_context import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/tenants")
@require_super_admin()
async def list_tenants(
    is_active: bool | None = None,
    education_type: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """List all tenants with optional filters (Super Admin only)."""
    tenant_service = get_tenant_service()
    tenants, total = await tenant_service.get_tenants(
        db,
        is_active=is_active,
        education_type=education_type,
        search=search,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        status="success",
        data=[TenantListItem.model_validate(t) for t in tenants],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post("/tenants")
@require_super_admin()
async def create_tenant(
    request: TenantCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Create a new tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    tenant = await tenant_service.create_tenant(
        db,
        name=request.name,
        email=request.email,
        education_type=request.education_type,
        phone=request.phone,
        address=request.address,
        slug=request.slug,
    )

    return APIResponse(
        status="success",
        data=TenantResponse.model_validate(tenant),
        message="Tenant created successfully",
    )


@router.get("/tenants/{tenant_id}")
@require_super_admin()
async def get_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get a tenant by ID (Super Admin only)."""
    tenant_service = get_tenant_service()
    tenant = await tenant_service.get_tenant(db, tenant_id)

    return APIResponse(
        status="success",
        data=TenantResponse.model_validate(tenant),
    )


@router.put("/tenants/{tenant_id}")
@require_super_admin()
async def update_tenant(
    tenant_id: uuid.UUID,
    request: TenantUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Update a tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    tenant = await tenant_service.update_tenant(
        db,
        tenant_id,
        name=request.name,
        email=request.email,
        phone=request.phone,
        address=request.address,
        is_active=request.is_active,
        settings=request.settings,
    )

    return APIResponse(
        status="success",
        data=TenantResponse.model_validate(tenant),
        message="Tenant updated successfully",
    )


@router.delete("/tenants/{tenant_id}")
@require_super_admin()
async def delete_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Soft delete a tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    await tenant_service.delete_tenant(db, tenant_id)

    return APIResponse(
        status="success",
        message="Tenant deleted successfully",
    )


@router.get("/tenants/{tenant_id}/stats")
@require_super_admin()
async def get_tenant_stats(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get statistics for a specific tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    stats = await tenant_service.get_tenant_stats(db, tenant_id)

    return APIResponse(
        status="success",
        data=TenantStatsResponse(**stats),
    )


@router.get("/tenants/{tenant_id}/admins")
@require_super_admin()
async def get_tenant_admins(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get all admin users for a tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    admins = await tenant_service.get_tenant_admins(db, tenant_id)

    return APIResponse(
        status="success",
        data=[TenantAdminResponse.model_validate(a) for a in admins],
    )


@router.post("/tenants/{tenant_id}/admins")
@require_super_admin()
async def create_tenant_admin(
    tenant_id: uuid.UUID,
    request: TenantAdminCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Create an admin user for a tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    admin = await tenant_service.create_tenant_admin(
        db,
        tenant_id=tenant_id,
        email=request.email,
        password=request.password,
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
    )

    return APIResponse(
        status="success",
        data=TenantAdminResponse.model_validate(admin),
        message="Admin user created successfully",
    )


@router.get("/stats")
@require_super_admin()
async def get_platform_stats(
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get platform-wide statistics (Super Admin only)."""
    tenant_service = get_tenant_service()
    stats = await tenant_service.get_platform_stats(db)

    # Remove recent_tenants for API response (it contains ORM objects)
    api_stats = {k: v for k, v in stats.items() if k != "recent_tenants"}

    return APIResponse(
        status="success",
        data=PlatformStatsResponse(**api_stats),
    )


# --- Email Settings (SMTP / Resend) ---


class EmailConfigRequest(BaseModel):
    """Request schema for email configuration."""

    provider: str = Field("smtp", pattern="^(smtp|resend)$")
    enabled: bool = True
    from_email: str = Field(..., min_length=1, max_length=255)
    from_name: str = Field("ClassUp", max_length=255)
    # SMTP fields
    smtp_host: str = Field("", max_length=255)
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_username: str = Field("", max_length=255)
    smtp_password: str = Field("", max_length=255)
    smtp_use_tls: bool = True
    # Resend fields
    resend_api_key: str = Field("", max_length=255)


MASKED = "********"


@router.get("/email-settings")
@require_super_admin()
async def get_email_settings(
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get current email settings (secrets masked)."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == EMAIL_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()

    if not row:
        return APIResponse(status="success", data=None)

    config = dict(row.value)
    if config.get("smtp_password"):
        config["smtp_password"] = MASKED
    if config.get("resend_api_key"):
        config["resend_api_key"] = MASKED

    return APIResponse(status="success", data=config)


@router.put("/email-settings")
@require_super_admin()
async def update_email_settings(
    request: EmailConfigRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Save or update email settings."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == EMAIL_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()

    config = request.model_dump()

    # Preserve existing secrets if the masked placeholder was sent back
    if row:
        if config["smtp_password"] == MASKED:
            config["smtp_password"] = row.value.get("smtp_password", "")
        if config["resend_api_key"] == MASKED:
            config["resend_api_key"] = row.value.get("resend_api_key", "")

    if row:
        row.value = config
    else:
        row = SystemSettings(key=EMAIL_CONFIG_KEY, value=config)
        db.add(row)

    await db.flush()

    # Mask secrets in response
    resp = dict(config)
    if resp.get("smtp_password"):
        resp["smtp_password"] = MASKED
    if resp.get("resend_api_key"):
        resp["resend_api_key"] = MASKED

    return APIResponse(
        status="success",
        data=resp,
        message="Email settings saved successfully",
    )


class TestEmailRequest(BaseModel):
    """Optional recipient for the test email."""

    to: str | None = Field(None, max_length=255)


@router.post("/email-settings/test")
@require_super_admin()
async def test_email_settings(
    body: TestEmailRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Send a test email to a given address or the super admin's own email."""
    from app.models.user import User

    recipient = body.to.strip() if body and body.to and body.to.strip() else None

    if not recipient:
        user_id = get_current_user_id()
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            return APIResponse(status="error", message="User not found")
        recipient = user.email
        recipient_name = user.first_name
    else:
        recipient_name = "Admin"

    email_service = get_email_service()
    result = await email_service.send(
        to=recipient,
        subject="ClassUp Test Email",
        template_name="welcome.html",
        context={
            "user_name": recipient_name,
            "tenant_name": "ClassUp Platform",
            "login_url": f"{get_settings().app_base_url}/login",
            "app_name": "ClassUp",
        },
    )

    if result:
        return APIResponse(
            status="success",
            message=f"Test email sent to {recipient}",
        )
    return APIResponse(
        status="error",
        message="Failed to send test email. Check settings and server logs.",
    )
