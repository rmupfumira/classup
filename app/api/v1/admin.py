"""Super Admin API routes for tenant management."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
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
from app.services.tenant_service import get_tenant_service
from app.utils.permissions import require_super_admin

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
