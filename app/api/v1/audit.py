"""Super admin audit-log API."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import APIResponse, PaginationMeta
from app.services.audit_service import get_audit_service
from app.utils.permissions import require_super_admin


logger = logging.getLogger(__name__)
router = APIRouter(tags=["Audit"])


class AuditConfigUpdate(BaseModel):
    enabled: bool | None = None
    level: str | None = Field(
        None,
        description="One of MINIMAL, STANDARD, VERBOSE",
    )
    retention_days: int | None = Field(None, ge=1, le=3650)


class PurgeRequest(BaseModel):
    retention_days: int | None = Field(None, ge=1, le=3650)


def _event_to_dict(e) -> dict:
    return {
        "id": str(e.id),
        "tenant_id": str(e.tenant_id) if e.tenant_id else None,
        "tenant_name": e.tenant_name,
        "user_id": str(e.user_id) if e.user_id else None,
        "user_email": e.user_email,
        "user_name": e.user_name,
        "user_role": e.user_role,
        "action": e.action,
        "resource_type": e.resource_type,
        "resource_id": e.resource_id,
        "method": e.method,
        "path": e.path,
        "status_code": e.status_code,
        "ip_address": e.ip_address,
        "user_agent": e.user_agent,
        "details": e.details,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/admin/audit-config")
@require_super_admin()
async def get_audit_config(db: AsyncSession = Depends(get_db)) -> APIResponse:
    service = get_audit_service()
    return APIResponse(status="success", data=await service.get_config(db))


@router.put("/admin/audit-config")
@require_super_admin()
async def update_audit_config(
    body: AuditConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    service = get_audit_service()
    try:
        cfg = await service.update_config(
            db,
            enabled=body.enabled,
            level=body.level,
            retention_days=body.retention_days,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return APIResponse(
        status="success",
        message="Audit configuration saved",
        data=cfg,
    )


@router.get("/admin/audit-events")
@require_super_admin()
async def list_audit_events(
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    since_minutes: int | None = Query(None, ge=1, le=10080, description="Only events from the last N minutes"),
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """List audit events with filters and pagination."""
    service = get_audit_service()
    since_dt: datetime | None = None
    if since_minutes:
        since_dt = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    items, total = await service.list_events(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        since=since_dt,
        search=search,
        page=page,
        page_size=page_size,
    )
    total_pages = (total + page_size - 1) // page_size
    return APIResponse(
        status="success",
        data=[_event_to_dict(e) for e in items],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get("/admin/online-users")
@require_super_admin()
async def list_online_users(
    threshold_minutes: int = Query(5, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Users seen in the last N minutes (default 5)."""
    service = get_audit_service()
    users = await service.get_online_users(db, threshold_minutes=threshold_minutes)
    return APIResponse(status="success", data=users)


@router.post("/admin/audit-events/purge")
@require_super_admin()
async def purge_audit_events(
    body: PurgeRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Manually purge old audit events."""
    service = get_audit_service()
    count = await service.purge_old(db, retention_days=body.retention_days)
    await db.commit()
    return APIResponse(
        status="success",
        message=f"Purged {count} old audit entries",
        data={"deleted": count},
    )
