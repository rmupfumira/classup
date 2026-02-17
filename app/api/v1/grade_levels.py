"""API endpoints for managing grade levels."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.grade_level import (
    GradeLevelCreate,
    GradeLevelUpdate,
    GradeLevelResponse,
    GradeLevelListResponse,
)
from app.services.grade_level_service import get_grade_level_service
from app.utils.permissions import require_role

router = APIRouter()


@router.get("", response_model=dict)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def list_grade_levels(
    is_active: bool | None = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List all grade levels for the current tenant."""
    service = get_grade_level_service()
    grade_levels, total = await service.get_grade_levels(
        db, is_active=is_active, page=page, page_size=page_size
    )

    return {
        "status": "success",
        "data": [GradeLevelListResponse.model_validate(gl) for gl in grade_levels],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total,
            "total_pages": (total + page_size - 1) // page_size,
            "has_next": page * page_size < total,
            "has_prev": page > 1,
        },
    }


@router.get("/templates", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def get_grade_level_templates():
    """Get available grade level templates for selection.

    Returns predefined grade level options that admins can choose from
    when creating new grade levels.
    """
    service = get_grade_level_service()
    templates = await service.get_available_grade_level_templates()

    return {
        "status": "success",
        "data": templates,
    }


@router.get("/{grade_level_id}", response_model=dict)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def get_grade_level(
    grade_level_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a grade level by ID."""
    service = get_grade_level_service()
    grade_level = await service.get_grade_level(db, grade_level_id)

    if not grade_level:
        raise HTTPException(status_code=404, detail="Grade level not found")

    return {
        "status": "success",
        "data": GradeLevelResponse.model_validate(grade_level),
    }


@router.post("", response_model=dict, status_code=201)
@require_role("SCHOOL_ADMIN")
async def create_grade_level(
    data: GradeLevelCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new grade level."""
    service = get_grade_level_service()

    # Check if code already exists
    existing = await service.get_grade_level_by_code(db, data.code)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Grade level with code '{data.code}' already exists",
        )

    grade_level = await service.create_grade_level(
        db,
        name=data.name,
        code=data.code,
        description=data.description,
        display_order=data.display_order,
    )

    return {
        "status": "success",
        "data": GradeLevelResponse.model_validate(grade_level),
        "message": "Grade level created successfully",
    }


@router.put("/{grade_level_id}", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def update_grade_level(
    grade_level_id: uuid.UUID,
    data: GradeLevelUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a grade level."""
    service = get_grade_level_service()

    # Check if code is being changed and if it conflicts
    if data.code:
        existing = await service.get_grade_level_by_code(db, data.code)
        if existing and existing.id != grade_level_id:
            raise HTTPException(
                status_code=409,
                detail=f"Grade level with code '{data.code}' already exists",
            )

    grade_level = await service.update_grade_level(
        db,
        grade_level_id,
        **data.model_dump(exclude_unset=True),
    )

    if not grade_level:
        raise HTTPException(status_code=404, detail="Grade level not found")

    return {
        "status": "success",
        "data": GradeLevelResponse.model_validate(grade_level),
        "message": "Grade level updated successfully",
    }


@router.delete("/{grade_level_id}", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def delete_grade_level(
    grade_level_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Soft delete a grade level."""
    service = get_grade_level_service()
    deleted = await service.delete_grade_level(db, grade_level_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Grade level not found")

    return {
        "status": "success",
        "message": "Grade level deleted successfully",
    }
