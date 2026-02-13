"""Email service using Resend for transactional emails."""

import logging
from pathlib import Path
from typing import Any

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EmailService:
    """Service for sending transactional emails via Resend."""

    def __init__(self):
        """Initialize the email service."""
        resend.api_key = settings.resend_api_key
        self.from_address = f"{settings.email_from_name} <{settings.email_from_address}>"

        # Set up Jinja2 environment for email templates
        templates_path = Path(__file__).parent.parent / "templates" / "emails"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(templates_path)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def _render_template(self, template_name: str, context: dict[str, Any]) -> str:
        """Render an email template with the given context."""
        template = self.jinja_env.get_template(template_name)
        return template.render(**context)

    async def send(
        self,
        to: str | list[str],
        subject: str,
        template_name: str,
        context: dict[str, Any],
        reply_to: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> str | None:
        """
        Send an email using a Jinja2 template.

        Args:
            to: Recipient email address(es)
            subject: Email subject line
            template_name: Name of the template file (e.g., "welcome.html")
            context: Template context variables
            reply_to: Optional reply-to address
            cc: Optional CC recipients
            bcc: Optional BCC recipients

        Returns:
            Email ID if successful, None if failed
        """
        try:
            # Render the HTML template
            html_body = self._render_template(template_name, context)

            # Prepare email params
            params: dict[str, Any] = {
                "from": self.from_address,
                "to": to if isinstance(to, list) else [to],
                "subject": subject,
                "html": html_body,
            }

            if reply_to:
                params["reply_to"] = reply_to
            if cc:
                params["cc"] = cc
            if bcc:
                params["bcc"] = bcc

            # Send via Resend
            result = resend.Emails.send(params)

            logger.info(f"Email sent successfully: {result.get('id')}")
            return result.get("id")

        except Exception as e:
            logger.error(f"Failed to send email to {to}: {str(e)}")
            return None

    async def send_welcome_email(
        self,
        to: str,
        user_name: str,
        tenant_name: str,
        login_url: str,
    ) -> str | None:
        """Send a welcome email to a new user."""
        return await self.send(
            to=to,
            subject=f"Welcome to {tenant_name} on ClassUp!",
            template_name="welcome.html",
            context={
                "user_name": user_name,
                "tenant_name": tenant_name,
                "login_url": login_url,
                "app_name": settings.app_name,
            },
        )

    async def send_parent_invitation(
        self,
        to: str,
        tenant_name: str,
        student_name: str,
        invitation_code: str,
        register_url: str,
        expires_in_days: int = 7,
    ) -> str | None:
        """Send an invitation email to a parent."""
        return await self.send(
            to=to,
            subject=f"You're invited to join {tenant_name} on ClassUp",
            template_name="parent_invite.html",
            context={
                "tenant_name": tenant_name,
                "student_name": student_name,
                "invitation_code": invitation_code,
                "register_url": register_url,
                "expires_in_days": expires_in_days,
                "app_name": settings.app_name,
            },
        )

    async def send_password_reset(
        self,
        to: str,
        user_name: str,
        reset_url: str,
        expires_in_hours: int = 24,
    ) -> str | None:
        """Send a password reset email."""
        return await self.send(
            to=to,
            subject="Reset your ClassUp password",
            template_name="password_reset.html",
            context={
                "user_name": user_name,
                "reset_url": reset_url,
                "expires_in_hours": expires_in_hours,
                "app_name": settings.app_name,
            },
        )

    async def send_report_ready(
        self,
        to: str,
        parent_name: str,
        student_name: str,
        report_type: str,
        report_date: str,
        view_url: str,
        tenant_name: str,
    ) -> str | None:
        """Send a notification that a report is ready."""
        return await self.send(
            to=to,
            subject=f"New {report_type} for {student_name}",
            template_name="report_ready.html",
            context={
                "parent_name": parent_name,
                "student_name": student_name,
                "report_type": report_type,
                "report_date": report_date,
                "view_url": view_url,
                "tenant_name": tenant_name,
                "app_name": settings.app_name,
            },
        )

    async def send_attendance_alert(
        self,
        to: str,
        parent_name: str,
        student_name: str,
        status: str,
        date: str,
        tenant_name: str,
        notes: str | None = None,
    ) -> str | None:
        """Send an attendance alert to parents."""
        return await self.send(
            to=to,
            subject=f"Attendance Alert: {student_name} marked {status}",
            template_name="attendance_alert.html",
            context={
                "parent_name": parent_name,
                "student_name": student_name,
                "status": status,
                "date": date,
                "notes": notes,
                "tenant_name": tenant_name,
                "app_name": settings.app_name,
            },
        )

    async def send_admin_notification(
        self,
        to: str,
        admin_name: str,
        notification_type: str,
        title: str,
        body: str,
        tenant_name: str,
        action_url: str | None = None,
    ) -> str | None:
        """Send an admin notification email."""
        return await self.send(
            to=to,
            subject=f"[{tenant_name}] {title}",
            template_name="admin_notification.html",
            context={
                "admin_name": admin_name,
                "notification_type": notification_type,
                "title": title,
                "body": body,
                "action_url": action_url,
                "tenant_name": tenant_name,
                "app_name": settings.app_name,
            },
        )


# Singleton instance
_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    """Get the email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
