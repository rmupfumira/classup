"""Service layer for business logic."""

from app.services.attendance_service import AttendanceService, get_attendance_service
from app.services.auth_service import AuthService, get_auth_service
from app.services.class_service import ClassService, get_class_service
from app.services.email_service import EmailService, get_email_service
from app.services.file_service import FileService, get_file_service
from app.services.i18n_service import I18nService, get_i18n_service
from app.services.import_service import ImportService, get_import_service
from app.services.invitation_service import InvitationService, get_invitation_service
from app.services.onboarding_service import OnboardingService, get_onboarding_service
from app.services.message_service import MessageService, get_message_service
from app.services.notification_service import NotificationService, get_notification_service
from app.services.realtime_service import ConnectionManager, get_connection_manager
from app.services.report_service import ReportService, get_report_service
from app.services.student_service import StudentService, get_student_service
from app.services.user_service import UserService, get_user_service
from app.services.webhook_service import WebhookService, get_webhook_service
from app.services.whatsapp_service import WhatsAppService, get_whatsapp_service

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
    "FileService",
    "get_file_service",
    "ReportService",
    "get_report_service",
    "EmailService",
    "get_email_service",
    "NotificationService",
    "get_notification_service",
    "ConnectionManager",
    "get_connection_manager",
    "WhatsAppService",
    "get_whatsapp_service",
    "InvitationService",
    "get_invitation_service",
    "WebhookService",
    "get_webhook_service",
    "ImportService",
    "get_import_service",
    "I18nService",
    "get_i18n_service",
    "OnboardingService",
    "get_onboarding_service",
]
