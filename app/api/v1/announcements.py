"""Announcement API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.announcement import AnnouncementCreate, AnnouncementResponse, AnnouncementUpdate
from app.schemas.common import APIResponse, PaginationMeta
from app.services.announcement_service import get_announcement_service
from app.utils.permissions import require_role
from app.utils.tenant_context import get_current_user_id

router = APIRouter()


def _build_announcement_response(announcement) -> AnnouncementResponse:
    """Build announcement response with related data."""
    return AnnouncementResponse(
        id=announcement.id,
        tenant_id=announcement.tenant_id,
        title=announcement.title,
        body=announcement.body,
        level=announcement.level,
        severity=announcement.severity,
        class_id=announcement.class_id,
        expires_at=announcement.expires_at,
        is_pinned=announcement.is_pinned,
        created_by=announcement.created_by,
        created_at=announcement.created_at,
        updated_at=announcement.updated_at,
        creator_name=(
            f"{announcement.creator.first_name} {announcement.creator.last_name}"
            if announcement.creator else None
        ),
        class_name=announcement.school_class.name if announcement.school_class else None,
        is_expired=announcement.is_expired,
        is_active=announcement.is_active,
    )


@router.post("", response_model=APIResponse[AnnouncementResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def create_announcement(
    data: AnnouncementCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new announcement."""
    service = get_announcement_service()
    announcement = await service.create_announcement(db, data.model_dump())
    return APIResponse(
        data=_build_announcement_response(announcement),
        message="Announcement created successfully",
    )


@router.get("", response_model=APIResponse[list[AnnouncementResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def list_announcements(
    level: str | None = Query(None, description="Filter by level: SCHOOL or CLASS"),
    severity: str | None = Query(None, description="Filter by severity"),
    class_id: uuid.UUID | None = Query(None, description="Filter by class ID"),
    active_only: bool = Query(False, description="Only show active announcements"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List announcements with optional filters."""
    service = get_announcement_service()
    announcements, total = await service.get_announcements(
        db,
        level=level,
        severity=severity,
        class_id=class_id,
        active_only=active_only,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data=[_build_announcement_response(a) for a in announcements],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get("/{announcement_id}", response_model=APIResponse[AnnouncementResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_announcement(
    announcement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single announcement."""
    service = get_announcement_service()
    announcement = await service.get_announcement(db, announcement_id)
    return APIResponse(data=_build_announcement_response(announcement))


@router.put("/{announcement_id}", response_model=APIResponse[AnnouncementResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def update_announcement(
    announcement_id: uuid.UUID,
    data: AnnouncementUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an announcement."""
    service = get_announcement_service()
    announcement = await service.update_announcement(
        db, announcement_id, data.model_dump(exclude_unset=True),
    )
    return APIResponse(
        data=_build_announcement_response(announcement),
        message="Announcement updated successfully",
    )


@router.delete("/{announcement_id}", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def delete_announcement(
    announcement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete an announcement."""
    service = get_announcement_service()
    await service.delete_announcement(db, announcement_id)
    return APIResponse(message="Announcement deleted successfully")


@router.post("/{announcement_id}/dismiss", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def dismiss_announcement(
    announcement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Dismiss an announcement for the current user."""
    service = get_announcement_service()
    user_id = get_current_user_id()
    await service.dismiss_announcement(db, announcement_id, user_id)
    return APIResponse(message="Announcement dismissed")
