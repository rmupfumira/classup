"""Document sharing API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.document_share import (
    DocumentFileResponse,
    DocumentShareCreate,
    DocumentShareListItem,
    DocumentShareResponse,
    TaggedStudentResponse,
)
from app.services.document_service import get_document_service
from app.services.file_service import get_file_service
from app.utils.permissions import require_role

router = APIRouter()


def _build_doc_file(dsf, file_service) -> DocumentFileResponse:
    """Build a document file response with presigned URLs."""
    fe = dsf.file_entity
    return DocumentFileResponse(
        file_entity_id=fe.id,
        original_name=fe.original_name,
        content_type=fe.content_type,
        file_size=fe.file_size,
        view_url=file_service.generate_presigned_url(fe, expires_in=3600, inline=True),
        download_url=file_service.generate_presigned_url(fe, expires_in=3600),
    )


def _build_response(share, file_service) -> DocumentShareResponse:
    """Build a full document share response with presigned URLs."""
    files = [_build_doc_file(dsf, file_service) for dsf in (share.files or [])]
    tagged = [
        TaggedStudentResponse(
            student_id=tag.student_id,
            student_name=f"{tag.student.first_name} {tag.student.last_name}" if tag.student else "Unknown",
        )
        for tag in (share.tags or [])
    ]
    return DocumentShareResponse(
        id=share.id,
        scope=share.scope,
        class_name=share.class_name,
        title=share.title,
        description=share.description,
        sharer_name=share.sharer_name,
        file_count=share.file_count,
        files=files,
        tagged_students=tagged,
        created_at=share.created_at,
    )


def _build_list_item(share, file_service) -> DocumentShareListItem:
    """Build a list item response."""
    return DocumentShareListItem(
        id=share.id,
        scope=share.scope,
        class_name=share.class_name,
        title=share.title,
        description=share.description,
        sharer_name=share.sharer_name,
        file_count=share.file_count,
        primary_file_name=share.primary_file_name,
        tagged_student_names=share.tagged_student_names,
        created_at=share.created_at,
    )


@router.post("", response_model=APIResponse[DocumentShareResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def create_document_share(
    data: DocumentShareCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a document share with files and optional student tags."""
    service = get_document_service()
    file_service = get_file_service()
    share = await service.create_document_share(db, data.model_dump())
    return APIResponse(
        data=_build_response(share, file_service),
        message="Document shared successfully",
    )


@router.get("", response_model=APIResponse[list[DocumentShareListItem]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def list_document_shares(
    class_id: uuid.UUID | None = Query(None),
    scope: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List document shares with optional class and scope filters."""
    service = get_document_service()
    file_service = get_file_service()
    shares, total = await service.get_document_shares(
        db, class_id=class_id, scope=scope, page=page, page_size=page_size
    )
    total_pages = (total + page_size - 1) // page_size
    return APIResponse(
        data=[_build_list_item(s, file_service) for s in shares],
        pagination=PaginationMeta(
            page=page, page_size=page_size, total_items=total,
            total_pages=total_pages, has_next=page < total_pages, has_prev=page > 1,
        ),
    )


@router.get("/{share_id}", response_model=APIResponse[DocumentShareResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_document_share(
    share_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single document share with all details and URLs."""
    service = get_document_service()
    file_service = get_file_service()
    share = await service.get_document_share(db, share_id)
    return APIResponse(data=_build_response(share, file_service))


@router.delete("/{share_id}", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def delete_document_share(
    share_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Soft delete a document share."""
    service = get_document_service()
    await service.delete_document_share(db, share_id)
    return APIResponse(message="Document share deleted successfully")
