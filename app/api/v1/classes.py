"""School class API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.school_class import (
    AssignTeacherRequest,
    SchoolClassCreate,
    SchoolClassDetailResponse,
    SchoolClassListResponse,
    SchoolClassResponse,
    SchoolClassUpdate,
    StudentBasicInfo,
    TeacherInfo,
)
from app.services.class_service import get_class_service
from app.utils.permissions import require_role

router = APIRouter()


def _build_class_list_response(school_class) -> SchoolClassListResponse:
    """Build class list response with computed fields."""
    student_count = len([s for s in school_class.students if s.deleted_at is None and s.is_active])
    teacher_count = len(school_class.teacher_classes)

    primary_teacher_name = None
    for tc in school_class.teacher_classes:
        if tc.is_primary and tc.teacher:
            primary_teacher_name = f"{tc.teacher.first_name} {tc.teacher.last_name}"
            break
    if not primary_teacher_name and school_class.teacher_classes:
        tc = school_class.teacher_classes[0]
        if tc.teacher:
            primary_teacher_name = f"{tc.teacher.first_name} {tc.teacher.last_name}"

    # Get grade level name from relationship
    grade_level_name = None
    if school_class.grade_level_rel:
        grade_level_name = school_class.grade_level_rel.name

    return SchoolClassListResponse(
        id=school_class.id,
        name=school_class.name,
        description=school_class.description,
        age_group=school_class.age_group,  # DEPRECATED
        grade_level=school_class.grade_level,  # DEPRECATED
        grade_level_id=school_class.grade_level_id,
        grade_level_name=grade_level_name,
        capacity=school_class.capacity,
        is_active=school_class.is_active,
        student_count=student_count,
        teacher_count=teacher_count,
        primary_teacher_name=primary_teacher_name,
    )


def _build_class_response(school_class) -> SchoolClassResponse:
    """Build class response with computed fields."""
    student_count = len([s for s in school_class.students if s.deleted_at is None and s.is_active])
    teacher_count = len(school_class.teacher_classes)

    # Get grade level name from relationship
    grade_level_name = None
    if school_class.grade_level_rel:
        grade_level_name = school_class.grade_level_rel.name

    return SchoolClassResponse(
        id=school_class.id,
        tenant_id=school_class.tenant_id,
        name=school_class.name,
        description=school_class.description,
        age_group=school_class.age_group,  # DEPRECATED
        grade_level=school_class.grade_level,  # DEPRECATED
        grade_level_id=school_class.grade_level_id,
        grade_level_name=grade_level_name,
        capacity=school_class.capacity,
        is_active=school_class.is_active,
        created_at=school_class.created_at,
        updated_at=school_class.updated_at,
        student_count=student_count,
        teacher_count=teacher_count,
    )


def _build_class_detail_response(school_class) -> SchoolClassDetailResponse:
    """Build detailed class response with students and teachers."""
    base = _build_class_response(school_class)

    students = [
        StudentBasicInfo(
            id=s.id,
            first_name=s.first_name,
            last_name=s.last_name,
            photo_path=s.photo_path,
            is_active=s.is_active,
        )
        for s in school_class.students
        if s.deleted_at is None
    ]

    teachers = [
        TeacherInfo(
            id=tc.teacher.id,
            first_name=tc.teacher.first_name,
            last_name=tc.teacher.last_name,
            email=tc.teacher.email,
            is_primary=tc.is_primary,
        )
        for tc in school_class.teacher_classes
        if tc.teacher
    ]

    return SchoolClassDetailResponse(
        **base.model_dump(),
        students=students,
        teachers=teachers,
    )


@router.get("", response_model=APIResponse[list[SchoolClassListResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def list_classes(
    is_active: bool | None = Query(True, description="Filter by active status"),
    grade_level_id: uuid.UUID | None = Query(None, description="Filter by grade level ID"),
    age_group: str | None = Query(None, description="DEPRECATED: Filter by age group"),
    grade_level: str | None = Query(None, description="DEPRECATED: Filter by grade level string"),
    search: str | None = Query(None, description="Search by name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """List classes with optional filters.

    Teachers only see their assigned classes.
    School admins see all classes in their tenant.
    """
    service = get_class_service()
    classes, total = await service.get_classes(
        db,
        is_active=is_active,
        grade_level_id=grade_level_id,
        age_group=age_group,  # DEPRECATED
        grade_level=grade_level,  # DEPRECATED
        search=search,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data=[_build_class_list_response(c) for c in classes],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post("", response_model=APIResponse[SchoolClassResponse])
@require_role(Role.SCHOOL_ADMIN)
async def create_class(
    data: SchoolClassCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new school class (school admin only)."""
    service = get_class_service()
    school_class = await service.create_class(db, data)
    await db.commit()

    return APIResponse(
        data=_build_class_response(school_class),
        message="Class created successfully",
    )


@router.get("/my-classes", response_model=APIResponse[list[SchoolClassListResponse]])
@require_role(Role.TEACHER)
async def get_my_classes(
    db: AsyncSession = Depends(get_db),
):
    """Get the current teacher's assigned classes."""
    service = get_class_service()
    classes = await service.get_my_classes(db)

    return APIResponse(
        data=[_build_class_list_response(c) for c in classes],
    )


@router.get("/{class_id}", response_model=APIResponse[SchoolClassDetailResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_class(
    class_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single class by ID with students and teachers."""
    service = get_class_service()
    school_class = await service.get_class(db, class_id)

    return APIResponse(data=_build_class_detail_response(school_class))


@router.put("/{class_id}", response_model=APIResponse[SchoolClassResponse])
@require_role(Role.SCHOOL_ADMIN)
async def update_class(
    class_id: uuid.UUID,
    data: SchoolClassUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a class (school admin only)."""
    service = get_class_service()
    school_class = await service.update_class(db, class_id, data)
    await db.commit()

    return APIResponse(
        data=_build_class_response(school_class),
        message="Class updated successfully",
    )


@router.delete("/{class_id}", response_model=APIResponse[None])
@require_role(Role.SCHOOL_ADMIN)
async def delete_class(
    class_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Soft delete a class (school admin only)."""
    service = get_class_service()
    await service.delete_class(db, class_id)
    await db.commit()

    return APIResponse(message="Class deleted successfully")


@router.get("/{class_id}/students", response_model=APIResponse[list[StudentBasicInfo]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_class_students(
    class_id: uuid.UUID,
    is_active: bool | None = Query(True, description="Filter by active status"),
    db: AsyncSession = Depends(get_db),
):
    """Get all students in a class."""
    service = get_class_service()
    students = await service.get_class_students(db, class_id, is_active)

    return APIResponse(
        data=[
            StudentBasicInfo(
                id=s.id,
                first_name=s.first_name,
                last_name=s.last_name,
                photo_path=s.photo_path,
                is_active=s.is_active,
            )
            for s in students
        ]
    )


@router.get("/{class_id}/teachers", response_model=APIResponse[list[TeacherInfo]])
@require_role(Role.SCHOOL_ADMIN)
async def get_class_teachers(
    class_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get all teachers assigned to a class."""
    service = get_class_service()
    teachers_data = await service.get_class_teachers(db, class_id)

    return APIResponse(
        data=[
            TeacherInfo(
                id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                is_primary=tc.is_primary,
            )
            for user, tc in teachers_data
        ]
    )


@router.post("/{class_id}/teachers", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def assign_teacher(
    class_id: uuid.UUID,
    data: AssignTeacherRequest,
    db: AsyncSession = Depends(get_db),
):
    """Assign a teacher to a class (school admin only)."""
    service = get_class_service()
    assignment = await service.assign_teacher(db, class_id, data)
    await db.commit()

    return APIResponse(
        data={"id": str(assignment.id), "assigned": True},
        message="Teacher assigned successfully",
    )


@router.delete("/{class_id}/teachers/{teacher_id}", response_model=APIResponse[None])
@require_role(Role.SCHOOL_ADMIN)
async def remove_teacher(
    class_id: uuid.UUID,
    teacher_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Remove a teacher from a class (school admin only)."""
    service = get_class_service()
    await service.remove_teacher(db, class_id, teacher_id)
    await db.commit()

    return APIResponse(message="Teacher removed from class")


@router.put("/{class_id}/teachers/{teacher_id}/set-primary", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def set_primary_teacher(
    class_id: uuid.UUID,
    teacher_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Set a teacher as primary for a class (school admin only)."""
    service = get_class_service()
    assignment = await service.set_primary_teacher(db, class_id, teacher_id)
    await db.commit()

    return APIResponse(
        data={"is_primary": assignment.is_primary},
        message="Primary teacher updated",
    )


@router.post("/{class_id}/select", response_model=APIResponse[dict])
@require_role(Role.TEACHER)
async def select_class(
    class_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Set the active class for the current teacher.

    Stores the selected class ID in a cookie for session persistence.
    The teacher must be assigned to the class.
    """
    service = get_class_service()
    # get_my_classes returns only classes assigned to the current teacher
    my_classes = await service.get_my_classes(db)
    assigned_ids = {c.id for c in my_classes}

    if class_id not in assigned_ids:
        from app.exceptions import ForbiddenException
        raise ForbiddenException("You are not assigned to this class")

    response = JSONResponse(
        content={
            "status": "success",
            "data": {"selected_class_id": str(class_id)},
            "message": "Class selected",
        }
    )
    response.set_cookie(
        key="selected_class_id",
        value=str(class_id),
        httponly=False,  # JS needs to read this for UI updates
        samesite="lax",
        max_age=60 * 60 * 24 * 365,  # 1 year
    )
    return response
