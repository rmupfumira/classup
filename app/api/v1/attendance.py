"""Attendance API endpoints."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.attendance import AttendanceStatus
from app.models.user import Role
from app.schemas.attendance import (
    AttendanceRecordCreate,
    AttendanceRecordResponse,
    AttendanceRecordUpdate,
    AttendanceStatsResponse,
    BulkAttendanceCreate,
    BulkAttendanceResponse,
    ClassAttendanceForDate,
    StudentAttendanceSummary,
)
from app.schemas.common import APIResponse, PaginationMeta
from app.services.attendance_service import get_attendance_service
from app.utils.permissions import require_role


router = APIRouter()


def _build_attendance_response(record) -> AttendanceRecordResponse:
    """Build attendance record response with related data."""
    return AttendanceRecordResponse(
        id=record.id,
        tenant_id=record.tenant_id,
        student_id=record.student_id,
        class_id=record.class_id,
        date=record.date,
        status=record.status,
        check_in_time=record.check_in_time,
        check_out_time=record.check_out_time,
        recorded_by=record.recorded_by,
        notes=record.notes,
        created_at=record.created_at,
        updated_at=record.updated_at,
        student_name=f"{record.student.first_name} {record.student.last_name}" if record.student else None,
        class_name=record.school_class.name if record.school_class else None,
        recorded_by_name=f"{record.recorded_by_user.first_name} {record.recorded_by_user.last_name}" if record.recorded_by_user else None,
    )


@router.get("", response_model=APIResponse[list[AttendanceRecordResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def list_attendance(
    class_id: uuid.UUID | None = Query(None, description="Filter by class ID"),
    student_id: uuid.UUID | None = Query(None, description="Filter by student ID"),
    date_from: date | None = Query(None, description="Filter from date"),
    date_to: date | None = Query(None, description="Filter to date"),
    status: AttendanceStatus | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """List attendance records with optional filters.

    Teachers only see attendance for their assigned classes.
    """
    service = get_attendance_service()
    records, total = await service.get_attendance_records(
        db,
        class_id=class_id,
        student_id=student_id,
        date_from=date_from,
        date_to=date_to,
        status=status,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data=[_build_attendance_response(r) for r in records],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post("", response_model=APIResponse[AttendanceRecordResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def create_attendance(
    data: AttendanceRecordCreate,
    db: AsyncSession = Depends(get_db),
):
    """Record attendance for a single student."""
    service = get_attendance_service()
    record = await service.create_attendance_record(db, data)
    await db.commit()

    # Refresh to load relationships
    await db.refresh(record)

    return APIResponse(
        data=_build_attendance_response(record),
        message="Attendance recorded successfully",
    )


@router.post("/bulk", response_model=APIResponse[BulkAttendanceResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def bulk_attendance(
    data: BulkAttendanceCreate,
    db: AsyncSession = Depends(get_db),
):
    """Record attendance for multiple students at once.

    This is the primary endpoint for daily attendance taking.
    If a record already exists for a student on the given date, it will be updated.
    """
    service = get_attendance_service()
    result = await service.record_bulk_attendance(db, data)
    await db.commit()

    return APIResponse(
        data=result,
        message=f"Attendance recorded: {result.success_count} successful, {result.error_count} errors",
    )


@router.get("/class/{class_id}/date/{target_date}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_class_attendance_for_date(
    class_id: uuid.UUID,
    target_date: date,
    db: AsyncSession = Depends(get_db),
):
    """Get attendance data for a class on a specific date.

    Returns all students in the class with their attendance status for the day.
    This is used to populate the daily attendance form.
    """
    service = get_attendance_service()
    data = await service.get_class_attendance_for_date(db, class_id, target_date)

    return APIResponse(data=data)


@router.get("/student/{student_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_student_attendance(
    student_id: uuid.UUID,
    date_from: date | None = Query(None, description="Filter from date"),
    date_to: date | None = Query(None, description="Filter to date"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(30, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """Get attendance history for a specific student.

    Parents can only view their own children's attendance.
    """
    # TODO: Add parent permission check (verify student is their child)

    service = get_attendance_service()
    records, total, summary = await service.get_student_attendance_history(
        db,
        student_id=student_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data={
            "records": [_build_attendance_response(r) for r in records],
            "summary": summary.model_dump(),
        },
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get("/stats", response_model=APIResponse[AttendanceStatsResponse])
@require_role(Role.SCHOOL_ADMIN)
async def get_attendance_stats(
    class_id: uuid.UUID | None = Query(None, description="Filter by class ID"),
    date_from: date | None = Query(None, description="Filter from date"),
    date_to: date | None = Query(None, description="Filter to date"),
    db: AsyncSession = Depends(get_db),
):
    """Get attendance statistics.

    Provides aggregate attendance data for reporting.
    """
    service = get_attendance_service()
    stats = await service.get_attendance_stats(
        db,
        class_id=class_id,
        date_from=date_from,
        date_to=date_to,
    )

    return APIResponse(data=stats)


@router.get("/{record_id}", response_model=APIResponse[AttendanceRecordResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_attendance_record(
    record_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single attendance record by ID."""
    service = get_attendance_service()
    record = await service.get_attendance_record(db, record_id)

    return APIResponse(data=_build_attendance_response(record))


@router.put("/{record_id}", response_model=APIResponse[AttendanceRecordResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def update_attendance(
    record_id: uuid.UUID,
    data: AttendanceRecordUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an attendance record.

    Can be used to update status, check-out time, or notes.
    """
    service = get_attendance_service()
    record = await service.update_attendance_record(db, record_id, data)
    await db.commit()

    # Refresh to load relationships
    await db.refresh(record)

    return APIResponse(
        data=_build_attendance_response(record),
        message="Attendance updated successfully",
    )
