"""Pydantic schemas for request/response validation."""

from app.schemas.attendance import (
    AttendanceRecordCreate,
    AttendanceRecordResponse,
    AttendanceRecordUpdate,
    AttendanceStatsResponse,
    AttendanceStatus,
    BulkAttendanceCreate,
    BulkAttendanceRecord,
    BulkAttendanceResponse,
    ClassAttendanceForDate,
    StudentAttendanceSummary,
)
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    RefreshTokenRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    UpdateProfileRequest,
    UserProfile,
    VerifyInvitationRequest,
    VerifyInvitationResponse,
)
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.school_class import (
    SchoolClassCreate,
    SchoolClassResponse,
    SchoolClassUpdate,
)
from app.schemas.student import (
    EmergencyContact,
    StudentCreate,
    StudentResponse,
    StudentUpdate,
)
from app.schemas.user import UserCreate, UserResponse, UserUpdate

__all__ = [
    # Auth
    "LoginRequest",
    "LoginResponse",
    "RegisterRequest",
    "RegisterResponse",
    "RefreshTokenRequest",
    "ForgotPasswordRequest",
    "ForgotPasswordResponse",
    "ResetPasswordRequest",
    "ChangePasswordRequest",
    "UserProfile",
    "UpdateProfileRequest",
    "VerifyInvitationRequest",
    "VerifyInvitationResponse",
    # Common
    "APIResponse",
    "PaginationMeta",
    # User
    "UserCreate",
    "UserResponse",
    "UserUpdate",
    # Student
    "StudentCreate",
    "StudentResponse",
    "StudentUpdate",
    "EmergencyContact",
    # School Class
    "SchoolClassCreate",
    "SchoolClassResponse",
    "SchoolClassUpdate",
    # Attendance
    "AttendanceStatus",
    "AttendanceRecordCreate",
    "AttendanceRecordUpdate",
    "AttendanceRecordResponse",
    "BulkAttendanceRecord",
    "BulkAttendanceCreate",
    "BulkAttendanceResponse",
    "AttendanceStatsResponse",
    "StudentAttendanceSummary",
    "ClassAttendanceForDate",
]
