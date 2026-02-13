"""Service layer for business logic."""

from app.services.attendance_service import AttendanceService, get_attendance_service
from app.services.auth_service import AuthService, get_auth_service
from app.services.class_service import ClassService, get_class_service
from app.services.message_service import MessageService, get_message_service
from app.services.student_service import StudentService, get_student_service
from app.services.user_service import UserService, get_user_service

__all__ = [
    "AuthService",
    "get_auth_service",
    "UserService",
    "get_user_service",
    "StudentService",
    "get_student_service",
    "ClassService",
    "get_class_service",
    "AttendanceService",
    "get_attendance_service",
    "MessageService",
    "get_message_service",
]
