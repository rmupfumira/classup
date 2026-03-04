"""Photo sharing API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.photo_share import (
    PhotoFileResponse,
    PhotoShareCreate,
    PhotoShareListItem,
    PhotoShareResponse,
    TaggedStudentResponse,
)
from app.services.file_service import get_file_service
from app.services.photo_service import get_photo_service
from app.utils.permissions import require_role

router = APIRouter()


def _build_photo_file(psf, file_service) -> PhotoFileResponse:
    """Build a photo file response with presigned URLs."""
    fe = psf.file_entity
    return PhotoFileResponse(
        file_entity_id=fe.id,
        original_name=fe.original_name,
        thumbnail_url=file_service.generate_presigned_url(fe, expires_in=3600),
        full_url=file_service.generate_presigned_url(fe, expires_in=3600),
    )


def _build_response(share, file_service) -> PhotoShareResponse:
    """Build a full photo share response with presigned URLs."""
    photos = [_build_photo_file(psf, file_service) for psf in (share.files or [])]
    tagged = [
        TaggedStudentResponse(
            student_id=tag.student_id,
            student_name=f"{tag.student.first_name} {tag.student.last_name}" if tag.student else "Unknown",
        )
        for tag in (share.tags or [])
    ]
    return PhotoShareResponse(
        id=share.id,
        class_name=share.class_name,
        caption=share.caption,
        sharer_name=share.sharer_name,
        photo_count=share.photo_count,
        photos=photos,
        tagged_students=tagged,
        created_at=share.created_at,
    )


def _build_list_item(share, file_service) -> PhotoShareListItem:
    """Build a list item response with thumbnail."""
    thumbnail_url = None
    if share.files:
        fe = share.files[0].file_entity
        thumbnail_url = file_service.generate_presigned_url(fe, expires_in=3600)
    return PhotoShareListItem(
        id=share.id,
        class_name=share.class_name,
        caption=share.caption,
        sharer_name=share.sharer_name,
        photo_count=share.photo_count,
        thumbnail_url=thumbnail_url,
        tagged_student_names=share.tagged_student_names,
        created_at=share.created_at,
    )


@router.post("", response_model=APIResponse[PhotoShareResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def create_photo_share(
    data: PhotoShareCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a photo share with files and student tags."""
    service = get_photo_service()
    file_service = get_file_service()
    share = await service.create_photo_share(db, data.model_dump())
    return APIResponse(
        data=_build_response(share, file_service),
        message="Photos shared successfully",
    )


@router.get("", response_model=APIResponse[list[PhotoShareListItem]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def list_photo_shares(
    class_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List photo shares (gallery API) with optional class filter."""
    service = get_photo_service()
    file_service = get_file_service()
    shares, total = await service.get_photo_shares(db, class_id=class_id, page=page, page_size=page_size)
    total_pages = (total + page_size - 1) // page_size
    return APIResponse(
        data=[_build_list_item(s, file_service) for s in shares],
        pagination=PaginationMeta(
            page=page, page_size=page_size, total_items=total,
            total_pages=total_pages, has_next=page < total_pages, has_prev=page > 1,
        ),
    )


@router.get("/{share_id}", response_model=APIResponse[PhotoShareResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_photo_share(
    share_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single photo share with all details."""
    service = get_photo_service()
    file_service = get_file_service()
    share = await service.get_photo_share(db, share_id)
    return APIResponse(data=_build_response(share, file_service))


@router.delete("/{share_id}", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def delete_photo_share(
    share_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Soft delete a photo share."""
    service = get_photo_service()
    await service.delete_photo_share(db, share_id)
    return APIResponse(message="Photo share deleted successfully")
