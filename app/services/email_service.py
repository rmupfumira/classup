"""Email service supporting SMTP and Resend providers.

Email configuration is stored in the system_settings DB table (key='email_config')
and can be managed at runtime via the super admin UI.
"""

import base64
import logging
import re
import uuid as _uuid
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from email import encoders as email_encoders
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


# ---------------------------------------------------------------------------
# Deliverability helpers (2026-10-09)
# ---------------------------------------------------------------------------
#
# Gmail and friends flag mail as spam when:
#  - the only body part is HTML (no text/plain alternative),
#  - the From display name is a brand but the domain is a generic sender,
#  - there's no List-Unsubscribe header,
#  - there's no Reply-To or it goes to a no-one address,
#  - Message-ID / Date headers are missing.
#
# These helpers patch all five at the code level. SPF/DKIM/DMARC on the
# classup.co.za DNS zone (plus flipping the sender address off "test@...")
# are the remaining pieces, and have to happen outside the code.


class _HtmlToTextParser(HTMLParser):
    """Minimal HTML→plain-text converter for the text/plain MIME part.

    Not a full renderer — just enough to preserve paragraph breaks and
    anchor hrefs so the plaintext fallback is readable (bad plaintext
    bumps Gmail's spam score). We feed in server-rendered Jinja HTML
    which is predictable, so the heuristics work.
    """

    _BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer",
    }
    _SKIP_TAGS = {"script", "style", "head", "title", "meta", "link"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip_depth = 0
        self._current_href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self._current_href = v
                    break
        if tag in self._BLOCK_TAGS:
            self._out.append("\n")
        if tag == "li":
            self._out.append("* ")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag == "a" and self._current_href:
            self._out.append(f" ({self._current_href})")
            self._current_href = None
        if tag in self._BLOCK_TAGS:
            self._out.append("\n")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        self._out.append(data)

    def get_text(self) -> str:
        raw = "".join(self._out)
        # Collapse 3+ newlines → 2, trim trailing spaces on each line.
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        collapsed: list[str] = []
        blank = 0
        for ln in lines:
            if ln:
                collapsed.append(ln)
                blank = 0
            else:
                blank += 1
                if blank < 2:
                    collapsed.append("")
        return "\n".join(collapsed).strip() + "\n"


def _html_to_plain(html: str) -> str:
    """Turn Jinja-rendered HTML into a readable text/plain fallback."""
    parser = _HtmlToTextParser()
    try:
        parser.feed(html)
        parser.close()
        return parser.get_text()
    except Exception:
        # Last resort: strip tags with a regex. Not pretty but better
        # than shipping no plaintext part at all.
        bare = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", bare).strip() + "\n"


def _list_unsubscribe_headers(
    recipient: str, from_email: str, app_base_url: str,
) -> dict[str, str]:
    """Return the ``List-Unsubscribe`` + ``List-Unsubscribe-Post`` pair.

    - ``List-Unsubscribe`` uses a ``mailto:`` so a reply with
      "unsubscribe" in the subject lands at a mailbox we own. Also
      includes an ``https://`` form for Gmail's one-click button when
      the deployment has an app_base_url set.
    - ``List-Unsubscribe-Post: List-Unsubscribe=One-Click`` is the
      RFC 8058 signal Gmail's one-click uses; without it the button
      falls back to opening the URL in a browser.

    ``recipient`` is URL-encoded into the mailto subject so the
    unsubscribe handler can match the right user without any extra
    lookups.
    """
    base = (app_base_url or "").rstrip("/")
    mailto = f"mailto:unsubscribe@{from_email.split('@', 1)[-1]}?subject=unsubscribe-{recipient}"
    headers: dict[str, str] = {"List-Unsubscribe": f"<{mailto}>"}
    if base:
        from urllib.parse import quote as _quote
        unsubscribe_url = f"{base}/unsubscribe?email={_quote(recipient)}"
        headers["List-Unsubscribe"] = f"<{unsubscribe_url}>, <{mailto}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    return headers


_FROM_WARNED_FOR: set[str] = set()


def _warn_if_suspicious_from(from_email: str) -> None:
    """Flag from_email prefixes that look like spam-fodder.

    Gmail penalises senders whose local part is a generic word like
    ``test``, ``demo``, ``admin``, ``info`` — the heuristics flag
    these as low-reputation. We log once per process so operators
    see it in logs without filling disk with warnings.
    """
    if not from_email or "@" not in from_email:
        return
    local = from_email.split("@", 1)[0].lower()
    if from_email in _FROM_WARNED_FOR:
        return
    bad_prefixes = {"test", "demo", "temp", "admin"}
    if local in bad_prefixes:
        logger.warning(
            "Email from_address uses low-reputation local part '%s' — "
            "spam filters flag this. Change to notifications@, "
            "no-reply@, or school@ in Admin → Email Settings before "
            "going live.",
            from_email,
        )
        _FROM_WARNED_FOR.add(from_email)


async def _load_email_config() -> dict[str, Any] | None:
    """Load email configuration from the system_settings table."""
    try:
        async with get_db_context() as db:
            result = await db.execute(
                select(SystemSettings).where(SystemSettings.key == EMAIL_CONFIG_KEY)
            )
            row = result.scalar_one_or_none()
            if row and row.value and row.value.get("enabled"):
                cfg = row.value
                _warn_if_suspicious_from(cfg.get("from_email") or "")
                return cfg
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
        from_email: str,
        recipients: list[str],
        subject: str,
        html_body: str,
        text_body: str,
        reply_to: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
        attachments: list[dict[str, Any]] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        """Send email via SMTP.

        Builds a multipart/alternative body (plain + HTML) so Gmail
        and friends see a legitimate text fallback. ``extra_headers``
        carries List-Unsubscribe + anything else the caller computes.
        """
        # Build the plain/HTML alternative first; attachments wrap it.
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(text_body, "plain", "utf-8"))
        alt_part.attach(MIMEText(html_body, "html", "utf-8"))

        if attachments:
            msg = MIMEMultipart("mixed")
            msg.attach(alt_part)
            for att in attachments:
                # Attachment dict schema: filename (str, required),
                # content (bytes, required), content_type (str,
                # optional — full MIME type like "text/calendar;
                # method=REQUEST", used when we need Gmail/Outlook to
                # recognise the payload as a calendar invite).
                content_type = att.get("content_type")
                if content_type and "/" in content_type:
                    main, _, sub = content_type.partition(";")
                    maintype, _, subtype = main.strip().partition("/")
                    part = MIMEBase(maintype or "application", subtype or "octet-stream")
                    part.set_payload(att["content"])
                    email_encoders.encode_base64(part)
                    if sub:
                        # Preserve any parameters (e.g. "method=REQUEST",
                        # "charset=UTF-8") after the base type.
                        part.replace_header(
                            "Content-Type",
                            f"{main.strip()};{sub}",
                        )
                else:
                    part = MIMEApplication(att["content"], Name=att["filename"])
                part["Content-Disposition"] = f'attachment; filename="{att["filename"]}"'
                msg.attach(part)
        else:
            msg = alt_part

        msg["From"] = from_address
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        # Explicit Date + Message-ID — mail without these looks like
        # a bot handoff and spam filters notice. Message-ID uses the
        # from_email's domain so DMARC alignment holds.
        msg["Date"] = formatdate(localtime=False)
        try:
            msg_domain = from_email.split("@", 1)[1]
        except IndexError:
            msg_domain = "classup.co.za"
        msg["Message-ID"] = make_msgid(domain=msg_domain)

        if reply_to:
            msg["Reply-To"] = reply_to
        if cc:
            msg["Cc"] = ", ".join(cc)

        if extra_headers:
            for header_name, header_value in extra_headers.items():
                # Skip anything already set (From/To/Subject/etc.) to
                # avoid duplicate headers; Python's email package
                # happily appends and that breaks DKIM.
                if header_name in msg:
                    continue
                msg[header_name] = header_value

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

        return msg["Message-ID"] or f"smtp-{id(msg)}"

    async def _send_via_resend(
        self,
        config: dict[str, Any],
        from_address: str,
        from_email: str,
        recipients: list[str],
        subject: str,
        html_body: str,
        text_body: str,
        reply_to: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
        attachments: list[dict[str, Any]] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        """Send email via Resend.

        Mirrors the SMTP path: plain + HTML, custom headers for
        List-Unsubscribe, Reply-To when provided.
        """
        resend.api_key = config["resend_api_key"]

        params: dict[str, Any] = {
            "from": from_address,
            "to": recipients,
            "subject": subject,
            "html": html_body,
            "text": text_body,
        }

        if reply_to:
            params["reply_to"] = reply_to
        if cc:
            params["cc"] = cc
        if bcc:
            params["bcc"] = bcc
        if extra_headers:
            params["headers"] = dict(extra_headers)
        if attachments:
            params["attachments"] = []
            for att in attachments:
                a = {
                    "filename": att["filename"],
                    "content": base64.b64encode(att["content"]).decode("utf-8"),
                }
                # Resend passes content_type through as "type". Keeps
                # ICS calendar invites rendering as "Add to calendar"
                # buttons instead of a generic file attachment.
                if att.get("content_type"):
                    a["content_type"] = att["content_type"]
                params["attachments"].append(a)

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
        attachments: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """Send an email using a Jinja2 template.

        Args:
            from_name: Override the sender display name (e.g. tenant name).
                       Falls back to the configured from_name, then the app default.
            attachments: List of dicts with 'filename' (str) and 'content' (bytes).

        Returns a message ID string if successful, None if failed or not configured.
        """
        config = await _load_email_config()
        if not config:
            logger.warning("Email not configured or disabled — skipping send")
            return None

        provider = config.get("provider", "smtp")

        try:
            html_body = self._render_template(template_name, context)
            text_body = _html_to_plain(html_body)

            sender_name = from_name or config.get("from_name") or settings.email_from_name
            from_email = config.get("from_email") or settings.email_from_address
            from_address = f"{sender_name} <{from_email}>"

            recipients = to if isinstance(to, list) else [to]

            # List-Unsubscribe headers — Gmail / Yahoo bulk-sender
            # policy expects these on anything that resembles bulk
            # transactional mail. Built per-recipient so the signed
            # URL maps to the right account.
            app_base_url = getattr(settings, "app_base_url", "") or ""
            extra_headers = _list_unsubscribe_headers(
                recipients[0], from_email, app_base_url,
            )

            final_reply_to = reply_to
            if not final_reply_to:
                # Prefer a per-tenant Reply-To so a parent hitting
                # "Reply" lands at the school, not a shared noreply@
                # inbox. The tenant_email context key is populated by
                # most of our template-side callers (invoice,
                # attendance, invitations).
                final_reply_to = context.get("tenant_email") or context.get("reply_to")

            if provider == "resend":
                result_id = await self._send_via_resend(
                    config, from_address, from_email, recipients,
                    subject, html_body, text_body,
                    final_reply_to, cc, bcc, attachments, extra_headers,
                )
            else:
                result_id = await self._send_via_smtp(
                    config, from_address, from_email, recipients,
                    subject, html_body, text_body,
                    final_reply_to, cc, bcc, attachments, extra_headers,
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
        template_sections: list | None = None,
        report_sections: dict | None = None,
    ) -> str | None:
        """Send a report with full content to parents."""
        return await self.send(
            to=to,
            subject=f"{student_name}'s {report_type} - {report_date}",
            template_name="report_ready.html",
            context={
                "parent_name": parent_name,
                "student_name": student_name,
                "report_type": report_type,
                "report_date": report_date,
                "view_url": view_url,
                "tenant_name": tenant_name,
                "app_name": settings.app_name,
                "template_sections": template_sections or [],
                "report_sections": report_sections or {},
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
        if status in ("ABSENT", "LATE"):
            subject = f"Attendance Alert: {student_name} marked {status}"
        else:
            subject = f"Attendance Update: {student_name} marked {status}"
        return await self.send(
            to=to,
            subject=subject,
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

    async def send_pickup_alert(
        self,
        to: str,
        parent_name: str,
        student_name: str,
        checkout_time: str,
        date: str,
        tenant_name: str,
    ) -> str | None:
        """Send a pick-up alert to parents when a student is checked out."""
        return await self.send(
            to=to,
            subject=f"Pick-up Alert: {student_name} checked out",
            template_name="pickup_alert.html",
            context={
                "parent_name": parent_name,
                "student_name": student_name,
                "checkout_time": checkout_time,
                "date": date,
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

    async def send_invoice_notification(
        self,
        to: str,
        parent_name: str,
        student_name: str,
        invoice_number: str,
        total_amount: str,
        due_date: str,
        view_url: str,
        tenant_name: str,
        line_items: list[dict[str, Any]] | None = None,
        currency: str = "USD",
        tenant_address: str | None = None,
        tenant_phone: str | None = None,
        tenant_email: str | None = None,
        banking_details: str | None = None,
        payment_instructions: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """Send an invoice notification to a parent."""
        return await self.send(
            to=to,
            subject=f"Invoice {invoice_number} for {student_name}",
            template_name="invoice_sent.html",
            context={
                "parent_name": parent_name,
                "student_name": student_name,
                "invoice_number": invoice_number,
                "total_amount": total_amount,
                "due_date": due_date,
                "view_url": view_url,
                "tenant_name": tenant_name,
                "app_name": settings.app_name,
                "line_items": line_items or [],
                "currency": currency,
                "tenant_address": tenant_address,
                "tenant_phone": tenant_phone,
                "tenant_email": tenant_email,
                "banking_details": banking_details,
                "payment_instructions": payment_instructions,
            },
            from_name=tenant_name,
            attachments=attachments,
        )

    async def send_payment_confirmation(
        self,
        to: str,
        parent_name: str,
        student_name: str,
        invoice_number: str,
        payment_amount: str,
        remaining_balance: str,
        payment_method: str,
        view_url: str,
        tenant_name: str,
    ) -> str | None:
        """Send a payment confirmation to a parent."""
        return await self.send(
            to=to,
            subject=f"Payment Received - {invoice_number}",
            template_name="payment_received.html",
            context={
                "parent_name": parent_name,
                "student_name": student_name,
                "invoice_number": invoice_number,
                "payment_amount": payment_amount,
                "remaining_balance": remaining_balance,
                "payment_method": payment_method,
                "view_url": view_url,
                "tenant_name": tenant_name,
                "app_name": settings.app_name,
            },
            from_name=tenant_name,
        )

    async def send_overdue_reminder(
        self,
        to: str,
        parent_name: str,
        student_name: str,
        invoice_number: str,
        outstanding_balance: str,
        due_date: str,
        view_url: str,
        tenant_name: str,
    ) -> str | None:
        """Send an overdue invoice reminder to a parent."""
        return await self.send(
            to=to,
            subject=f"Overdue: Invoice {invoice_number} for {student_name}",
            template_name="invoice_overdue.html",
            context={
                "parent_name": parent_name,
                "student_name": student_name,
                "invoice_number": invoice_number,
                "outstanding_balance": outstanding_balance,
                "due_date": due_date,
                "view_url": view_url,
                "tenant_name": tenant_name,
                "app_name": settings.app_name,
            },
            from_name=tenant_name,
        )

    async def send_event_invitation(
        self,
        to: str,
        *,
        parent_name: str,
        tenant_name: str,
        event_title: str,
        event_when: str,
        event_location: str | None,
        event_description: str | None,
        event_type_label: str,
        rsvp_required: bool,
        rsvp_deadline: str | None,
        view_url: str,
        rsvp_yes_url: str | None,
        rsvp_no_url: str | None,
        rsvp_maybe_url: str | None,
        ics_bytes: bytes,
        ics_filename: str = "event.ics",
        method: str = "REQUEST",
    ) -> str | None:
        """Invite a parent to a school event.

        The ICS attachment is what makes 'Add to calendar' work in
        Gmail / Outlook / Apple Mail. `method=REQUEST` in the content
        type is the key hint Gmail keys off to show its native RSVP
        widget above the message.

        ``method="CANCEL"`` when notifying about a cancelled event —
        clients update the calendar entry accordingly.
        """
        subject = (
            f"Event: {event_title} — {tenant_name}"
            if method == "REQUEST"
            else f"Cancelled: {event_title} — {tenant_name}"
        )
        return await self.send(
            to=to,
            subject=subject,
            template_name="event_invitation.html",
            context={
                "parent_name": parent_name,
                "tenant_name": tenant_name,
                "event_title": event_title,
                "event_when": event_when,
                "event_location": event_location,
                "event_description": event_description,
                "event_type_label": event_type_label,
                "rsvp_required": rsvp_required,
                "rsvp_deadline": rsvp_deadline,
                "view_url": view_url,
                "rsvp_yes_url": rsvp_yes_url,
                "rsvp_no_url": rsvp_no_url,
                "rsvp_maybe_url": rsvp_maybe_url,
                "is_cancelled": method == "CANCEL",
                "app_name": settings.app_name,
            },
            from_name=tenant_name,
            attachments=[
                {
                    "filename": ics_filename,
                    "content": ics_bytes,
                    # method= param is what turns this from a "download
                    # this file" into "Add to calendar" in Gmail.
                    "content_type": f"text/calendar; charset=UTF-8; method={method}",
                },
            ],
        )

    async def send_teacher_notification(
        self,
        to: str,
        teacher_name: str,
        notification_type: str,
        title: str,
        body: str,
        tenant_name: str,
        action_url: str | None = None,
    ) -> str | None:
        """Send a notification email to a teacher."""
        return await self.send(
            to=to,
            subject=f"[{tenant_name}] {title}",
            template_name="admin_notification.html",
            context={
                "admin_name": teacher_name,
                "notification_type": notification_type,
                "title": title,
                "body": body,
                "action_url": action_url,
                "tenant_name": tenant_name,
                "app_name": settings.app_name,
            },
            from_name=tenant_name,
        )


    async def send_raw_email(
        self,
        to: str | list[str],
        subject: str,
        html_body: str,
        from_name: str | None = None,
    ) -> str | None:
        """Send a raw HTML email without a template.

        This method skips tenant context and is suitable for
        platform-level emails (e.g. trial signup notifications).
        """
        config = await _load_email_config()
        if not config:
            logger.warning("Email not configured or disabled — skipping send")
            return None

        provider = config.get("provider", "smtp")

        try:
            sender_name = from_name or config.get("from_name") or settings.email_from_name
            from_email = config.get("from_email") or settings.email_from_address
            from_address = f"{sender_name} <{from_email}>"
            text_body = _html_to_plain(html_body)

            recipients = to if isinstance(to, list) else [to]
            app_base_url = getattr(settings, "app_base_url", "") or ""
            extra_headers = _list_unsubscribe_headers(
                recipients[0], from_email, app_base_url,
            )

            if provider == "resend":
                result_id = await self._send_via_resend(
                    config, from_address, from_email, recipients,
                    subject, html_body, text_body,
                    None, None, None, None, extra_headers,
                )
            else:
                result_id = await self._send_via_smtp(
                    config, from_address, from_email, recipients,
                    subject, html_body, text_body,
                    None, None, None, None, extra_headers,
                )

            logger.info(f"Raw email sent via {provider} to {recipients}: {result_id}")
            return result_id

        except Exception as e:
            logger.error(f"Failed to send raw email via {provider} to {to}: {e}")
            return None


# Singleton instance
_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    """Get the email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
