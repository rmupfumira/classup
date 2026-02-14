"""Attendance-related Pydantic schemas."""

import uuid
from datetime import date as date_type
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AttendanceStatus(str, Enum):
    """Attendance status options."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    EXCUSED = "EXCUSED"


class AttendanceRecordBase(BaseModel):
    """Base attendance record schema."""

    student_id: uuid.UUID
    status: AttendanceStatus = AttendanceStatus.ABSENT
    check_in_time: datetime | None = None
    check_out_time: datetime | None = None
    notes: str | None = None


class AttendanceRecordCreate(AttendanceRecordBase):
    """Schema for creating an attendance record."""

    class_id: uuid.UUID
    date: date_type = Field(default_factory=date_type.today)


class AttendanceRecordUpdate(BaseModel):
    """Schema for updating an attendance record."""

    status: AttendanceStatus | None = None
    check_in_time: datetime | None = None
    check_out_time: datetime | None = None
    notes: str | None = None


class AttendanceRecordResponse(BaseModel):
    """Schema for attendance record response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    student_id: uuid.UUID
    class_id: uuid.UUID
    date: date
    status: str
    check_in_time: datetime | None
    check_out_time: datetime | None
    recorded_by: uuid.UUID
    notes: str | None
    created_at: datetime
    updated_at: datetime

    # Populated from relationships
    student_name: str | None = None
    class_name: str | None = None
    recorded_by_name: str | None = None


class BulkAttendanceRecord(BaseModel):
    """Single record for bulk attendance submission."""

    student_id: uuid.UUID
    status: AttendanceStatus
    check_in_time: datetime | None = None
    notes: str | None = None


class BulkAttendanceCreate(BaseModel):
    """Schema for bulk attendance submission."""

    class_id: uuid.UUID
    date: date = Field(default_factory=date.today)
    records: list[BulkAttendanceRecord]


class BulkAttendanceResponse(BaseModel):
    """Response for bulk attendance submission."""

    success_count: int
    error_count: int
    errors: list[dict] = []


class AttendanceStatsResponse(BaseModel):
    """Attendance statistics response."""

    total_students: int
    present_count: int
    absent_count: int
    late_count: int
    excused_count: int
    attendance_rate: float  # Percentage


class StudentAttendanceSummary(BaseModel):
    """Student attendance summary for a period."""

    student_id: uuid.UUID
    student_name: str
    total_days: int
    present_days: int
    absent_days: int
    late_days: int
    excused_days: int
    attendance_rate: float


class ClassAttendanceForDate(BaseModel):
    """Class attendance data for a specific date."""

    class_id: uuid.UUID
    class_name: str
    date: date
    students: list[dict]  # List of {student_id, student_name, status, check_in_time, notes}
    stats: AttendanceStatsResponse
