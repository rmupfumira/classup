"""Timetable API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.timetable import (
    GenerateDraftRequest,
    GenerateDraftResponse,
    TimetableConfigResponse,
    TimetableConfigUpdate,
    TimetableCreate,
    TimetableEntryResponse,
    TimetableEntryUpsert,
    TimetableListItem,
    TimetableResponse,
)
from app.services.timetable_service import get_timetable_service
from app.utils.permissions import require_role
from app.utils.tenant_context import get_current_user_id, get_current_user_role


router = APIRouter()


def _entry_to_response(entry, conflicts: set[tuple[str, int]] | None = None) -> TimetableEntryResponse:
    """Build an entry response with relationship data and conflict flag."""
    has_conflict = False
    if conflicts is not None:
        has_conflict = (entry.day, entry.period_index) in conflicts
    return TimetableEntryResponse(
        id=entry.id,
        day=entry.day,
        period_index=entry.period_index,
        subject_id=entry.subject_id,
        subject_name=entry.subject.name if entry.subject else None,
        teacher_id=entry.teacher_id,
        teacher_name=(
            f"{entry.teacher.first_name} {entry.teacher.last_name}"
            if entry.teacher
            else None
        ),
        has_conflict=has_conflict,
    )


# ---------------- Config ----------------


@router.get("/config", response_model=APIResponse[TimetableConfigResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_config(db: AsyncSession = Depends(get_db)):
    """Get the tenant's school-day config. Creates a default on first access."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    config = await service.get_or_create_config(db)
    await db.commit()
    return APIResponse(
        data=TimetableConfigResponse(
            id=config.id,
            days=config.days,
            periods=config.periods,
            updated_at=config.updated_at,
        )
    )


@router.put("/config", response_model=APIResponse[TimetableConfigResponse])
@require_role(Role.SCHOOL_ADMIN)
async def update_config(
    body: TimetableConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update the tenant's school-day config."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    periods_dicts = [p.model_dump() for p in body.periods]
    config = await service.update_config(db, body.days, periods_dicts)
    await db.commit()
    return APIResponse(
        data=TimetableConfigResponse(
            id=config.id,
            days=config.days,
            periods=config.periods,
            updated_at=config.updated_at,
        ),
        message="Timetable config updated",
    )


# ---------------- Timetable CRUD ----------------


@router.get("/timetables", response_model=APIResponse[list[TimetableListItem]])
@require_role(Role.SCHOOL_ADMIN)
async def list_timetables(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all timetables for the tenant."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    timetables, total = await service.list_timetables(db, page=page, page_size=page_size)
    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data=[
            TimetableListItem(
                id=t.id,
                class_id=t.class_id,
                class_name=t.school_class.name if t.school_class else None,
                name=t.name,
                is_active=t.is_active,
                entry_count=len(t.entries),
                created_at=t.created_at,
            )
            for t in timetables
        ],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post("/timetables", response_model=APIResponse[TimetableResponse])
@require_role(Role.SCHOOL_ADMIN)
async def create_timetable(
    body: TimetableCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new timetable for a class."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    timetable = await service.create_timetable(db, body.class_id, body.name)
    await db.commit()
    timetable = await service.get_timetable(db, timetable.id)
    return APIResponse(
        data=TimetableResponse(
            id=timetable.id,
            class_id=timetable.class_id,
            class_name=timetable.school_class.name if timetable.school_class else None,
            name=timetable.name,
            is_active=timetable.is_active,
            entries=[_entry_to_response(e) for e in timetable.entries],
            created_at=timetable.created_at,
            updated_at=timetable.updated_at,
        ),
        message="Timetable created",
    )


@router.get("/timetables/{timetable_id}", response_model=APIResponse[TimetableResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_timetable(
    timetable_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single timetable with entries and conflict flags."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    timetable = await service.get_timetable(db, timetable_id)
    conflicts = await service.detect_conflicts(db, timetable_id)
    return APIResponse(
        data=TimetableResponse(
            id=timetable.id,
            class_id=timetable.class_id,
            class_name=timetable.school_class.name if timetable.school_class else None,
            name=timetable.name,
            is_active=timetable.is_active,
            entries=[_entry_to_response(e, conflicts) for e in timetable.entries],
            created_at=timetable.created_at,
            updated_at=timetable.updated_at,
        )
    )


@router.delete("/timetables/{timetable_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def delete_timetable(
    timetable_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a timetable."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    await service.delete_timetable(db, timetable_id)
    await db.commit()
    return APIResponse(data={"deleted": True}, message="Timetable deleted")


# ---------------- Entries ----------------


@router.post(
    "/timetables/{timetable_id}/entries",
    response_model=APIResponse[TimetableEntryResponse],
)
@require_role(Role.SCHOOL_ADMIN)
async def upsert_entry(
    timetable_id: uuid.UUID,
    body: TimetableEntryUpsert,
    db: AsyncSession = Depends(get_db),
):
    """Upsert a single lesson cell (day + period)."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    entry = await service.set_entry(
        db,
        timetable_id=timetable_id,
        day=body.day,
        period_index=body.period_index,
        subject_id=body.subject_id,
        teacher_id=body.teacher_id,
    )
    await db.commit()
    # Reload with relationships
    timetable = await service.get_timetable(db, timetable_id)
    fresh_entry = next(
        (e for e in timetable.entries if e.id == entry.id), entry
    )
    return APIResponse(
        data=_entry_to_response(fresh_entry),
        message="Cell saved",
    )


@router.delete(
    "/timetables/{timetable_id}/entries/{day}/{period_index}",
    response_model=APIResponse[dict],
)
@require_role(Role.SCHOOL_ADMIN)
async def clear_entry(
    timetable_id: uuid.UUID,
    day: str,
    period_index: int,
    db: AsyncSession = Depends(get_db),
):
    """Clear a single cell."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    deleted = await service.clear_entry(db, timetable_id, day, period_index)
    await db.commit()
    return APIResponse(data={"deleted": deleted})


# ---------------- Generator ----------------


@router.post(
    "/timetables/{timetable_id}/generate",
    response_model=APIResponse[GenerateDraftResponse],
)
@require_role(Role.SCHOOL_ADMIN)
async def generate_draft(
    timetable_id: uuid.UUID,
    body: GenerateDraftRequest,
    db: AsyncSession = Depends(get_db),
):
    """Run the smart draft generator."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    entries_created, warnings = await service.generate_draft(
        db, timetable_id, weekly_hours=body.weekly_hours or {}
    )
    await db.commit()
    return APIResponse(
        data=GenerateDraftResponse(
            entries_created=entries_created,
            warnings=warnings,
        ),
        message=f"Draft generated with {entries_created} slots",
    )


# ---------------- Views ----------------


@router.get("/my-schedule", response_model=APIResponse[list[TimetableEntryResponse]])
@require_role(Role.TEACHER, Role.SCHOOL_ADMIN)
async def my_schedule(db: AsyncSession = Depends(get_db)):
    """Return the current user's teaching schedule."""
    service = get_timetable_service()
    await service._require_feature_enabled(db)
    user_id = get_current_user_id()
    entries = await service.get_teacher_timetable(db, user_id)

    return APIResponse(
        data=[
            TimetableEntryResponse(
                id=e.id,
                day=e.day,
                period_index=e.period_index,
                subject_id=e.subject_id,
                subject_name=e.subject.name if e.subject else None,
                teacher_id=e.teacher_id,
                teacher_name=(
                    f"{e.teacher.first_name} {e.teacher.last_name}"
                    if e.teacher
                    else None
                ),
            )
            for e in entries
        ]
    )


@router.get(
    "/for-student/{student_id}", response_model=APIResponse[TimetableResponse | None]
)
@require_role(Role.PARENT, Role.SCHOOL_ADMIN, Role.TEACHER)
async def for_student(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return the active timetable for a student's class."""
    from app.models import ParentStudent

    service = get_timetable_service()
    await service._require_feature_enabled(db)

    # Parents can only view their own children
    role = get_current_user_role()
    if role == Role.PARENT.value:
        user_id = get_current_user_id()
        from sqlalchemy import select
        check = await db.execute(
            select(ParentStudent).where(
                ParentStudent.parent_id == user_id,
                ParentStudent.student_id == student_id,
            )
        )
        if not check.scalar_one_or_none():
            raise ForbiddenException("You can only view your own children")

    timetable = await service.get_student_timetable(db, student_id)
    if not timetable:
        return APIResponse(data=None, message="No active timetable for this student")

    return APIResponse(
        data=TimetableResponse(
            id=timetable.id,
            class_id=timetable.class_id,
            class_name=timetable.school_class.name if timetable.school_class else None,
            name=timetable.name,
            is_active=timetable.is_active,
            entries=[_entry_to_response(e) for e in timetable.entries],
            created_at=timetable.created_at,
            updated_at=timetable.updated_at,
        )
    )
