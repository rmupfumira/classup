"""SQLAlchemy models for ClassUp v2."""

from app.models.base import Base, BaseModel, TenantScopedModel, TimestampMixin, SoftDeleteMixin
from app.models.tenant import Tenant, EducationType, get_default_tenant_settings
from app.models.user import User, Role
from app.models.student import Student, ParentStudent, Gender, AgeGroup
from app.models.school_class import SchoolClass, TeacherClass
from app.models.attendance import AttendanceRecord, AttendanceStatus
from app.models.report import (
    DailyReport,
    ReportTemplate,
    ReportTemplateGradeLevel,
    ReportType,
    ReportFrequency,
    ReportStatus,
    get_default_daycare_template_sections,
)
from app.models.grade_level import GradeLevel
from app.models.file_entity import FileEntity, FileCategory
from app.models.invitation import ParentInvitation, InvitationStatus, generate_invitation_code
from app.models.notification import Notification, NotificationType
from app.models.webhook import (
    WebhookEndpoint,
    WebhookEvent,
    WebhookEventType,
    WebhookEventStatus,
    generate_webhook_secret,
)
from app.models.import_job import BulkImportJob, ImportType, ImportStatus
from app.models.academic import Subject, ClassSubject, GradingSystem

__all__ = [
    # Base
    "Base",
    "BaseModel",
    "TenantScopedModel",
    "TimestampMixin",
    "SoftDeleteMixin",
    # Tenant
    "Tenant",
    "EducationType",
    "get_default_tenant_settings",
    # User
    "User",
    "Role",
    # Student
    "Student",
    "ParentStudent",
    "Gender",
    "AgeGroup",
    # School Class
    "SchoolClass",
    "TeacherClass",
    # Attendance
    "AttendanceRecord",
    "AttendanceStatus",
    # Report
    "DailyReport",
    "ReportTemplate",
    "ReportTemplateGradeLevel",
    "ReportType",
    "ReportFrequency",
    "ReportStatus",
    "get_default_daycare_template_sections",
    # Grade Level
    "GradeLevel",
    # File
    "FileEntity",
    "FileCategory",
    # Invitation
    "ParentInvitation",
    "InvitationStatus",
    "generate_invitation_code",
    # Notification
    "Notification",
    "NotificationType",
    # Webhook
    "WebhookEndpoint",
    "WebhookEvent",
    "WebhookEventType",
    "WebhookEventStatus",
    "generate_webhook_secret",
    # Import
    "BulkImportJob",
    "ImportType",
    "ImportStatus",
    # Academic
    "Subject",
    "ClassSubject",
    "GradingSystem",
]
