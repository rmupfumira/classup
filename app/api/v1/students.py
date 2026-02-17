"""Student API endpoints."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.student import (
    LinkParentRequest,
    ParentInfo,
    StudentCreate,
    StudentDetailResponse,
    StudentListResponse,
    StudentResponse,
    StudentUpdate,
)
from app.services.student_service import get_student_service
from app.utils.permissions import require_role
from app.utils.tenant_context import get_current_user_id, get_current_user_role

router = APIRouter()


def _build_student_list_response(student) -> StudentListResponse:
    """Build student list response with computed fields."""
    # Get effective grade level from class
    effective_grade_level_id = None
    effective_grade_level_name = None
    if student.school_class and student.school_class.grade_level_id:
        effective_grade_level_id = student.school_class.grade_level_id
        if hasattr(student.school_class, 'grade_level_rel') and student.school_class.grade_level_rel:
            effective_grade_level_name = student.school_class.grade_level_rel.name

    return StudentListResponse(
        id=student.id,
        first_name=student.first_name,
        last_name=student.last_name,
        age_group=student.age_group,  # DEPRECATED
        grade_level=student.grade_level,  # DEPRECATED
        class_id=student.class_id,
        is_active=student.is_active,
        photo_path=student.photo_path,
        full_name=f"{student.first_name} {student.last_name}",
        class_name=student.school_class.name if student.school_class else None,
        effective_grade_level_id=effective_grade_level_id,
        effective_grade_level_name=effective_grade_level_name,
    )


def _build_student_response(student) -> StudentResponse:
    """Build student response with computed fields."""
    age = None
    if student.date_of_birth:
        today = date.today()
        age = today.year - student.date_of_birth.year
        if (today.month, today.day) < (student.date_of_birth.month, student.date_of_birth.day):
            age -= 1

    # Get effective grade level from class
    effective_grade_level_id = None
    effective_grade_level_name = None
    if student.school_class and student.school_class.grade_level_id:
        effective_grade_level_id = student.school_class.grade_level_id
        if hasattr(student.school_class, 'grade_level_rel') and student.school_class.grade_level_rel:
            effective_grade_level_name = student.school_class.grade_level_rel.name

    return StudentResponse(
        id=student.id,
        tenant_id=student.tenant_id,
        first_name=student.first_name,
        last_name=student.last_name,
        date_of_birth=student.date_of_birth,
        gender=student.gender,
        age_group=student.age_group,  # DEPRECATED
        grade_level=student.grade_level,  # DEPRECATED
        class_id=student.class_id,
        medical_info=student.medical_info,
        allergies=student.allergies,
        emergency_contacts=student.emergency_contacts or [],
        notes=student.notes,
        is_active=student.is_active,
        enrollment_date=student.enrollment_date,
        created_at=student.created_at,
        updated_at=student.updated_at,
        full_name=f"{student.first_name} {student.last_name}",
        age=age,
        class_name=student.school_class.name if student.school_class else None,
        effective_grade_level_id=effective_grade_level_id,
        effective_grade_level_name=effective_grade_level_name,
    )


def _build_student_detail_response(student) -> StudentDetailResponse:
    """Build detailed student response with parents."""
    base = _build_student_response(student)

    parents = []
    for ps in student.parent_students:
        if ps.parent:
            parents.append(ParentInfo(
                id=ps.parent.id,
                first_name=ps.parent.first_name,
                last_name=ps.parent.last_name,
                email=ps.parent.email,
                phone=ps.parent.phone,
                relationship_type=ps.relationship_type,
                is_primary=ps.is_primary,
            ))

    return StudentDetailResponse(
        **base.model_dump(),
        parents=parents,
    )


@router.get("", response_model=APIResponse[list[StudentListResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def list_students(
    class_id: uuid.UUID | None = Query(None, description="Filter by class ID"),
    grade_level_id: uuid.UUID | None = Query(None, description="Filter by grade level ID (via class)"),
    age_group: str | None = Query(None, description="DEPRECATED: Filter by age group"),
    is_active: bool | None = Query(True, description="Filter by active status"),
    search: str | None = Query(None, description="Search by name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """List students with optional filters.

    Teachers only see students in their assigned classes.
    School admins see all students in their tenant.
    """
    service = get_student_service()
    students, total = await service.get_students(
        db,
        class_id=class_id,
        grade_level_id=grade_level_id,
        age_group=age_group,  # DEPRECATED
        is_active=is_active,
        search=search,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data=[_build_student_list_response(s) for s in students],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post("", response_model=APIResponse[StudentResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def create_student(
    data: StudentCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new student."""
    service = get_student_service()
    student = await service.create_student(db, data)
    await db.commit()

    return APIResponse(
        data=_build_student_response(student),
        message="Student created successfully",
    )


@router.get("/my-children", response_model=APIResponse[list[StudentListResponse]])
@require_role(Role.PARENT)
async def get_my_children(
    db: AsyncSession = Depends(get_db),
):
    """Get the current parent's linked children."""
    parent_id = get_current_user_id()
    service = get_student_service()
    students = await service.get_my_children(db, parent_id)

    return APIResponse(
        data=[_build_student_list_response(s) for s in students],
    )


@router.get("/{student_id}", response_model=APIResponse[StudentDetailResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_student(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single student by ID.

    Parents can only view their own children.
    """
    role = get_current_user_role()
    user_id = get_current_user_id()
    service = get_student_service()

    student = await service.get_student(db, student_id)

    # Parents can only view their own children
    if role == Role.PARENT.value:
        parent_ids = [ps.parent_id for ps in student.parent_students]
        if user_id not in parent_ids:
            from app.exceptions import ForbiddenException
            raise ForbiddenException("You can only view your own children")

    return APIResponse(data=_build_student_detail_response(student))


@router.put("/{student_id}", response_model=APIResponse[StudentResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def update_student(
    student_id: uuid.UUID,
    data: StudentUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a student."""
    service = get_student_service()
    student = await service.update_student(db, student_id, data)
    await db.commit()

    return APIResponse(
        data=_build_student_response(student),
        message="Student updated successfully",
    )


@router.delete("/{student_id}", response_model=APIResponse[None])
@require_role(Role.SCHOOL_ADMIN)
async def delete_student(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Soft delete a student (school admin only)."""
    service = get_student_service()
    await service.delete_student(db, student_id)
    await db.commit()

    return APIResponse(message="Student deleted successfully")


@router.get("/{student_id}/parents", response_model=APIResponse[list[ParentInfo]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_student_parents(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get parents linked to a student."""
    service = get_student_service()
    parents_data = await service.get_student_parents(db, student_id)

    parents = [
        ParentInfo(
            id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            phone=user.phone,
            relationship_type=ps.relationship_type,
            is_primary=ps.is_primary,
        )
        for user, ps in parents_data
    ]

    return APIResponse(data=parents)


@router.post("/{student_id}/parents", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def link_parent(
    student_id: uuid.UUID,
    data: LinkParentRequest,
    db: AsyncSession = Depends(get_db),
):
    """Link a parent to a student."""
    service = get_student_service()
    link = await service.link_parent(db, student_id, data)
    await db.commit()

    return APIResponse(
        data={"id": str(link.id), "linked": True},
        message="Parent linked successfully",
    )


@router.delete("/{student_id}/parents/{parent_id}", response_model=APIResponse[None])
@require_role(Role.SCHOOL_ADMIN)
async def unlink_parent(
    student_id: uuid.UUID,
    parent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Unlink a parent from a student (school admin only)."""
    service = get_student_service()
    await service.unlink_parent(db, student_id, parent_id)
    await db.commit()

    return APIResponse(message="Parent unlinked successfully")
