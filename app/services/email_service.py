"""Email service supporting SMTP and Resend providers.

Email configuration is stored in the system_settings DB table (key='email_config')
and can be managed at runtime via the super admin UI.
"""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import aiosmtplib
import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db_context
from app.models.system_settings import SystemSettings

logger = logging.getLogger(__name__)
settings = get_settings()

EMAIL_CONFIG_KEY = "email_config"


async def _load_email_config() -> dict[str, Any] | None:
    """Load email configuration from the system_settings table."""
    try:
        async with get_db_context() as db:
            result = await db.execute(
                select(SystemSettings).where(SystemSettings.key == EMAIL_CONFIG_KEY)
            )
            row = result.scalar_one_or_none()
            if row and row.value and row.value.get("enabled"):
                return row.value
            return None
    except Exception as e:
        logger.error(f"Failed to load email config from DB: {e}")
        return None


class EmailService:
    """Service for sending transactional emails via SMTP or Resend."""

    def __init__(self):
        """Initialize the email service."""
        templates_path = Path(__file__).parent.parent / "templates" / "emails"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(templates_path)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def _render_template(self, template_name: str, context: dict[str, Any]) -> str:
        """Render an email template with the given context."""
        template = self.jinja_env.get_template(template_name)
        return template.render(**context)

    async def _send_via_smtp(
        self,
        config: dict[str, Any],
        from_address: str,
        recipients: list[str],
        subject: str,
        html_body: str,
        reply_to: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
    ) -> str:
        """Send email via SMTP."""
        msg = MIMEMultipart("alternative")
        msg["From"] = from_address
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        if reply_to:
            msg["Reply-To"] = reply_to
        if cc:
            msg["Cc"] = ", ".join(cc)

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        all_recipients = list(recipients)
        if cc:
            all_recipients.extend(cc)
        if bcc:
            all_recipients.extend(bcc)

        port = config.get("smtp_port", 587)
        use_starttls = config.get("smtp_use_tls", True)

        # Port 465 = implicit SSL, port 587 = STARTTLS
        if port == 465:
            tls_kwargs = {"use_tls": True, "start_tls": False}
        else:
            tls_kwargs = {"use_tls": False, "start_tls": use_starttls}

        await aiosmtplib.send(
            msg,
            hostname=config["smtp_host"],
            port=port,
            username=config.get("smtp_username") or None,
            password=config.get("smtp_password") or None,
            recipients=all_recipients,
            timeout=30,
            **tls_kwargs,
        )

        return f"smtp-{id(msg)}"

    async def _send_via_resend(
        self,
        config: dict[str, Any],
        from_address: str,
        recipients: list[str],
        subject: str,
        html_body: str,
        reply_to: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
    ) -> str:
        """Send email via Resend."""
        resend.api_key = config["resend_api_key"]

        params: dict[str, Any] = {
            "from": from_address,
            "to": recipients,
            "subject": subject,
            "html": html_body,
        }

        if reply_to:
            params["reply_to"] = reply_to
        if cc:
            params["cc"] = cc
        if bcc:
            params["bcc"] = bcc

        result = resend.Emails.send(params)
        return result.get("id", "resend-ok")

    async def send(
        self,
        to: str | list[str],
        subject: str,
        template_name: str,
        context: dict[str, Any],
        reply_to: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        from_name: str | None = None,
    ) -> str | None:
        """Send an email using a Jinja2 template.

        Args:
            from_name: Override the sender display name (e.g. tenant name).
                       Falls back to the configured from_name, then the app default.

        Returns a message ID string if successful, None if failed or not configured.
        """
        config = await _load_email_config()
        if not config:
            logger.warning("Email not configured or disabled — skipping send")
            return None

        provider = config.get("provider", "smtp")

        try:
            html_body = self._render_template(template_name, context)

            sender_name = from_name or config.get("from_name") or settings.email_from_name
            from_email = config.get("from_email") or settings.email_from_address
            from_address = f"{sender_name} <{from_email}>"

            recipients = to if isinstance(to, list) else [to]

            if provider == "resend":
                result_id = await self._send_via_resend(
                    config, from_address, recipients, subject, html_body,
                    reply_to, cc, bcc,
                )
            else:
                result_id = await self._send_via_smtp(
                    config, from_address, recipients, subject, html_body,
                    reply_to, cc, bcc,
                )

            logger.info(f"Email sent via {provider} to {recipients}: {result_id}")
            return result_id

        except Exception as e:
            logger.error(f"Failed to send email via {provider} to {to}: {e}")
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
            from_name=tenant_name,
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
            from_name=tenant_name,
        )

    async def send_teacher_invitation(
        self,
        to: str,
        tenant_name: str,
        teacher_name: str,
        invitation_code: str,
        register_url: str,
        expires_in_days: int = 7,
    ) -> str | None:
        """Send an invitation email to a teacher."""
        return await self.send(
            to=to,
            subject=f"You're invited to join {tenant_name} on ClassUp",
            template_name="teacher_invite.html",
            context={
                "tenant_name": tenant_name,
                "teacher_name": teacher_name,
                "invitation_code": invitation_code,
                "register_url": register_url,
                "expires_in_days": expires_in_days,
                "app_name": settings.app_name,
            },
            from_name=tenant_name,
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
            from_name=tenant_name,
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
            from_name=tenant_name,
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
            from_name=tenant_name,
        )

    async def notify_admins(
        self,
        db: AsyncSession,
        tenant_id: "Any",
        notification_type: str,
        title: str,
        body: str,
        action_url: str | None = None,
    ) -> None:
        """Send a notification email to all SCHOOL_ADMIN users in a tenant."""
        from app.models import Tenant, User
        from app.models.user import Role

        # Get tenant name
        tenant = await db.get(Tenant, tenant_id)
        tenant_name = tenant.name if tenant else "Your School"

        # Get all active school admins
        result = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.role == Role.SCHOOL_ADMIN.value,
                User.is_active == True,
                User.deleted_at.is_(None),
            )
        )
        admins = result.scalars().all()

        for admin in admins:
            try:
                await self.send_admin_notification(
                    to=admin.email,
                    admin_name=admin.first_name,
                    notification_type=notification_type,
                    title=title,
                    body=body,
                    tenant_name=tenant_name,
                    action_url=action_url,
                )
            except Exception:
                logger.exception(
                    f"Failed to send admin notification to {admin.email}"
                )


# Singleton instance
_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    """Get the email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
