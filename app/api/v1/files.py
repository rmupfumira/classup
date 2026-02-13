"""File API endpoints."""

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.file_entity import FileCategory
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.file import (
    FileEntityResponse,
    FileListResponse,
    FileUploadResponse,
    PresignedUrlResponse,
)
from app.services.file_service import get_file_service
from app.utils.permissions import require_role


router = APIRouter()


def _build_file_response(file_entity, download_url: str | None = None) -> FileUploadResponse:
    """Build file upload response."""
    return FileUploadResponse(
        id=file_entity.id,
        storage_path=file_entity.storage_path,
        original_name=file_entity.original_name,
        content_type=file_entity.content_type,
        file_size=file_entity.file_size,
        file_category=file_entity.file_category,
        uploaded_by=file_entity.uploaded_by,
        created_at=file_entity.created_at,
        file_size_human=file_entity.file_size_human,
        is_image=file_entity.is_image,
        is_pdf=file_entity.is_pdf,
        download_url=download_url,
    )


def _build_file_entity_response(file_entity, service) -> FileEntityResponse:
    """Build full file entity response with download URL."""
    download_url = service.generate_presigned_url(file_entity)
    return FileEntityResponse(
        id=file_entity.id,
        tenant_id=file_entity.tenant_id,
        storage_path=file_entity.storage_path,
        original_name=file_entity.original_name,
        content_type=file_entity.content_type,
        file_size=file_entity.file_size,
        file_category=file_entity.file_category,
        uploaded_by=file_entity.uploaded_by,
        created_at=file_entity.created_at,
        file_size_human=file_entity.file_size_human,
        is_image=file_entity.is_image,
        is_pdf=file_entity.is_pdf,
        download_url=download_url,
        uploader_name=f"{file_entity.uploader.first_name} {file_entity.uploader.last_name}" if file_entity.uploader else None,
    )


def _build_file_list_response(file_entity) -> FileListResponse:
    """Build simplified file list response."""
    return FileListResponse(
        id=file_entity.id,
        original_name=file_entity.original_name,
        content_type=file_entity.content_type,
        file_size=file_entity.file_size,
        file_category=file_entity.file_category,
        created_at=file_entity.created_at,
        is_image=file_entity.is_image,
    )


@router.post("/upload", response_model=APIResponse[FileUploadResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def upload_file(
    file: UploadFile = File(...),
    file_category: str = Form(...),
    entity_id: uuid.UUID | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file to storage.

    Supports photos (JPG, PNG, WebP, HEIC) and documents (PDF, Word).
    Max file size is configurable (default 10MB).

    Args:
        file: The file to upload
        file_category: PHOTO, DOCUMENT, AVATAR, or LOGO
        entity_id: Optional related entity ID
    """
    # Validate category
    try:
        category = FileCategory(file_category.upper())
    except ValueError:
        return APIResponse(
            status="error",
            message=f"Invalid file category. Must be one of: {', '.join(c.value for c in FileCategory)}",
        )

    service = get_file_service()
    file_entity = await service.upload_file(db, file, category, entity_id)
    await db.commit()
    await db.refresh(file_entity)

    # Generate presigned URL for immediate use
    download_url = service.generate_presigned_url(file_entity)

    return APIResponse(
        data=_build_file_response(file_entity, download_url),
        message="File uploaded successfully",
    )


@router.get("", response_model=APIResponse[list[FileListResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def list_files(
    category: str | None = Query(None, description="Filter by category (PHOTO, DOCUMENT, etc.)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List uploaded files with optional filtering."""
    file_category = None
    if category:
        try:
            file_category = FileCategory(category.upper())
        except ValueError:
            pass

    service = get_file_service()
    files, total = await service.get_files(db, category=file_category, page=page, page_size=page_size)

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data=[_build_file_list_response(f) for f in files],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get("/{file_id}", response_model=APIResponse[FileEntityResponse])
async def get_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get file details by ID."""
    service = get_file_service()
    file_entity = await service.get_file(db, file_id)

    return APIResponse(data=_build_file_entity_response(file_entity, service))


@router.get("/{file_id}/url", response_model=APIResponse[PresignedUrlResponse])
async def get_file_url(
    file_id: uuid.UUID,
    expires_in: int = Query(3600, ge=60, le=86400, description="URL expiry time in seconds (1 hour default)"),
    db: AsyncSession = Depends(get_db),
):
    """Get a presigned download URL for a file.

    The URL is valid for the specified duration (default 1 hour, max 24 hours).
    """
    service = get_file_service()
    file_entity = await service.get_file(db, file_id)
    url = service.generate_presigned_url(file_entity, expires_in)

    return APIResponse(
        data=PresignedUrlResponse(
            url=url,
            expires_in=expires_in,
            file_name=file_entity.original_name,
            content_type=file_entity.content_type,
        )
    )


@router.delete("/{file_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def delete_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a file.

    Only the uploader or an admin can delete a file.
    This performs a soft delete - the file is marked as deleted but not immediately removed from storage.
    """
    service = get_file_service()
    await service.delete_file(db, file_id)
    await db.commit()

    return APIResponse(
        data={"deleted": True},
        message="File deleted successfully",
    )
