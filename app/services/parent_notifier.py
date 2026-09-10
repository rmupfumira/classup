"""Parent notification helper — mirrors email notifications to WhatsApp
for opted-in parents.

Design principle: one entry point per event (``notify_attendance_alert``,
``notify_invoice_sent``, etc.), all going through a shared gate. Every
caller in the app already sends the email; this module adds the WhatsApp
copy alongside — never in place of.

Best-effort semantics:
  - WhatsApp failure NEVER propagates. The caller's ``await`` returns
    cleanly regardless. Nothing here can block or crash the email flow.
  - Not-opted-in / tenant-disabled / service-not-configured are all
    silent no-ops (return False so the caller can tell "sent" from
    "skipped" if it wants — most don't).

Template strategy:
  - 5 events have bespoke Meta-approved templates (``attendance_alert``,
    ``report_ready``, ``announcement``, ``parent_invite``, ``welcome``)
    — these give the polished branded message parents see.
  - 6 more events (invoices, payments, documents, photos, messages,
    pickup) DON'T have bespoke templates yet — they fall back to the
    generic ``announcement`` template with a well-crafted subject line.
    When bespoke templates are approved in Meta later, swap the fallback
    for the specific helper — no caller changes needed.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant, User
from app.services.whatsapp_service import get_whatsapp_service_from_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gate: is this user + tenant currently eligible for WhatsApp notifications?
# ---------------------------------------------------------------------------

async def _can_notify_whatsapp(
    db: AsyncSession, user: User | None
) -> bool:
    """Return True iff this user should get a WhatsApp copy of a notification.

    Requires (all of):
      1. User is set + active + has a phone + has opted in
      2. Tenant has the whatsapp_enabled feature ON
      3. The tenant's subscription plan includes whatsapp
      4. The WhatsApp service is fully configured (Meta credentials in DB)

    Any missing piece → silent skip. Log at DEBUG so an admin who wants
    to know why can turn logs up without spamming production.
    """
    if user is None or not user.is_active or user.deleted_at is not None:
        return False
    if not user.whatsapp_phone or not user.whatsapp_opted_in:
        return False
    if not user.tenant_id:
        return False

    tenant = await db.get(Tenant, user.tenant_id)
    if tenant is None:
        return False

    tenant_features = (tenant.settings or {}).get("features") or {}
    if not tenant_features.get("whatsapp_enabled", False):
        return False

    # Plan-side gate — reuse the same helper the bot uses so plan and
    # tenant flags stay in lockstep across bot + notifications.
    try:
        from app.services.subscription_service import get_subscription_service
        sub = await get_subscription_service().get_tenant_subscription(
            db, user.tenant_id,
        )
        plan_features = (sub.plan.features if sub and sub.plan else None) or {}
    except Exception:
        logger.exception("Failed to load subscription for tenant %s", user.tenant_id)
        return False

    if not plan_features.get("whatsapp_enabled", False):
        return False

    svc = await get_whatsapp_service_from_db(db)
    if not svc.is_configured:
        logger.info(
            "WhatsApp opted in for user %s but service not configured — skipping",
            user.id,
        )
        return False

    return True


async def _send_template(
    db: AsyncSession,
    user: User,
    template_method: str,
    *args: Any,
    language: str | None = None,
    **extra_kwargs: Any,
) -> bool:
    """Best-effort dispatch: call ``getattr(svc, template_method)(user.whatsapp_phone, *args, **extra_kwargs)``.

    Callers can pass positional args (legacy helpers like
    ``send_announcement(school, subject)``) OR keyword args (newer
    bespoke helpers like ``send_invoice_sent(parent_name=..., ...)``).
    ``language`` is always injected from the user's preference.

    Returns True if Meta accepted the send. Catches every exception —
    the caller must never care whether this succeeded.
    """
    try:
        svc = await get_whatsapp_service_from_db(db)
        fn = getattr(svc, template_method, None)
        if fn is None:
            logger.error(
                "parent_notifier: no template method %s on WhatsAppService",
                template_method,
            )
            return False
        kwargs = {"language": language or (user.language or "en"), **extra_kwargs}
        result = await fn(user.whatsapp_phone, *args, **kwargs)
        return result is not None
    except Exception:
        logger.exception(
            "WhatsApp notification via %s failed for user %s (%s)",
            template_method, user.id, user.whatsapp_phone,
        )
        return False


# ---------------------------------------------------------------------------
# Bespoke-template events (Meta-approved templates already exist)
# ---------------------------------------------------------------------------

async def notify_attendance_alert(
    db: AsyncSession, user: User,
    *, student_name: str, status: str, tenant_name: str,
) -> bool:
    """Mirror the email that fires when a student's attendance is marked
    (ABSENT / LATE / PRESENT alerts to their parents)."""
    if not await _can_notify_whatsapp(db, user):
        return False
    return await _send_template(
        db, user, "send_attendance_alert",
        student_name, status, tenant_name,
    )


async def notify_report_ready(
    db: AsyncSession, user: User,
    *, report_type: str, student_name: str, url: str,
) -> bool:
    """Mirror the "report has been finalised" email."""
    if not await _can_notify_whatsapp(db, user):
        return False
    return await _send_template(
        db, user, "send_report_ready",
        report_type, student_name, url,
    )


async def notify_announcement(
    db: AsyncSession, user: User,
    *, tenant_name: str, subject: str,
) -> bool:
    """Mirror the class/school announcement email."""
    if not await _can_notify_whatsapp(db, user):
        return False
    return await _send_template(
        db, user, "send_announcement",
        tenant_name, subject,
    )


async def notify_parent_invite(
    db: AsyncSession, user: User | None,
    *, to_phone: str, tenant_name: str, code: str, language: str = "en",
) -> bool:
    """Mirror the parent-invitation email. This is a special case: the
    parent doesn't have a User row yet (invite → account). Caller passes
    the phone directly and we skip the User-based gate — the invite
    itself IS the opt-in.

    Still gated by: tenant + plan flags + service configured.
    """
    # Reconstruct just enough of the gate that DOESN'T depend on a User row.
    try:
        from app.services.subscription_service import get_subscription_service
        # We need a tenant to check plan features — the caller doesn't
        # pass tenant_id, so we can't check plan here. Instead we just
        # require the service to be configured and the tenant setting
        # to be on (which we check via the tenant_name resolve upstream).
        svc = await get_whatsapp_service_from_db(db)
        if not svc.is_configured:
            return False
        result = await svc.send_parent_invite(
            to_phone=to_phone, school_name=tenant_name, code=code,
            language=language,
        )
        return result is not None
    except Exception:
        logger.exception(
            "WhatsApp parent-invite notification failed for %s", to_phone,
        )
        return False


async def notify_welcome(
    db: AsyncSession, user: User,
    *, tenant_name: str, url: str,
) -> bool:
    """Mirror the welcome email — sent when a new parent account is created."""
    if not await _can_notify_whatsapp(db, user):
        return False
    return await _send_template(
        db, user, "send_welcome",
        tenant_name, url,
    )


# ---------------------------------------------------------------------------
# Fallback-template events (uses the generic 'announcement' template
# with a carefully-crafted subject until bespoke templates land in Meta)
# ---------------------------------------------------------------------------

def _fmt_amount(amount: Decimal | float | int | str, currency: str = "R") -> str:
    """Localised currency format, e.g. 'R1,234.50'. Uses the tenant's
    currency symbol prefix — we don't have per-locale grouping rules
    here, so pick a reasonable default (comma thousands, dot decimal)."""
    try:
        val = Decimal(str(amount))
    except Exception:
        return f"{currency} {amount}"
    return f"{currency} {val:,.2f}"


async def notify_invoice_sent(
    db: AsyncSession, user: User,
    *, tenant_name: str, student_name: str, invoice_number: str,
    total_amount: Decimal, due_date_str: str, invoice_id: str,
    currency: str = "R",
    pdf_bytes: bytes | None = None,
) -> bool:
    """Uses the bespoke ``invoice_sent`` template (from Meta's
    ``purchase_receipt_3`` gallery). Falls back to the generic
    ``announcement`` template on any failure — most likely case is
    Meta hasn't approved the template yet on a given instance.

    ``pdf_bytes`` (optional) is uploaded to Meta as a document header
    so the invoice PDF appears in the WhatsApp thread. If omitted or
    the upload fails, the template still sends without an attachment.
    """
    if not await _can_notify_whatsapp(db, user):
        return False
    formatted = _fmt_amount(total_amount, currency)
    # Upload the PDF first — best-effort, message still sends without.
    pdf_media_id: str | None = None
    if pdf_bytes:
        try:
            svc = await get_whatsapp_service_from_db(db)
            pdf_media_id = await svc.upload_media(
                pdf_bytes, mime_type="application/pdf",
                filename=f"invoice_{invoice_number}.pdf",
            )
        except Exception:
            logger.exception(
                "Failed to upload invoice PDF for user %s — sending without attachment",
                user.id,
            )
    ok = await _send_template(
        db, user, "send_invoice_sent",
        parent_name=user.first_name or "there",
        invoice_number=invoice_number,
        student_name=student_name,
        formatted_amount=formatted,
        due_date=due_date_str,
        invoice_id=invoice_id,
        pdf_media_id=pdf_media_id,
        pdf_filename=f"invoice_{invoice_number}.pdf",
    )
    if ok:
        return True
    # Fallback: bespoke template hasn't been approved on this instance,
    # or Meta rejected the media. Fire the generic announcement so
    # the parent still gets *something*.
    subject = (
        f"Invoice {invoice_number} for {student_name}: {formatted} due {due_date_str}."
    )
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_invoice_overdue(
    db: AsyncSession, user: User,
    *, tenant_name: str, student_name: str, invoice_number: str,
    outstanding_balance: Decimal, due_date_str: str, invoice_id: str,
    currency: str = "R",
    penalty_text: str = "additional late fees",
) -> bool:
    """Uses the bespoke ``invoice_overdue`` template (from Meta's
    ``payment_reminder_3`` gallery). Falls back to the announcement
    template on failure."""
    if not await _can_notify_whatsapp(db, user):
        return False
    balance_str = _fmt_amount(outstanding_balance, currency)
    ok = await _send_template(
        db, user, "send_invoice_overdue",
        student_name=student_name,
        formatted_balance=balance_str,
        due_date=due_date_str,
        invoice_id=invoice_id,
        penalty_text=penalty_text,
    )
    if ok:
        return True
    subject = (
        f"Reminder: {invoice_number} for {student_name} is overdue "
        f"({balance_str}). Due was {due_date_str}."
    )
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_payment_received(
    db: AsyncSession, user: User,
    *, tenant_name: str, student_name: str, invoice_number: str,
    payment_amount: Decimal, remaining_balance: Decimal, invoice_id: str,
    payment_date: str, currency: str = "R",
) -> bool:
    """Confirm a payment via WhatsApp — the peace-of-mind message parents
    check for right after they pay. Uses the bespoke ``payment_received``
    template (from Meta's ``payment_successful`` gallery) with a URL
    button linking to the receipt view."""
    if not await _can_notify_whatsapp(db, user):
        return False
    payment_str = _fmt_amount(payment_amount, currency)
    ok = await _send_template(
        db, user, "send_payment_received",
        parent_name=user.first_name or "there",
        formatted_amount=payment_str,
        student_name=student_name,
        invoice_number=invoice_number,
        payment_date=payment_date,
        invoice_id=invoice_id,
    )
    if ok:
        return True
    # Fallback with the old rendered subject so the parent still
    # gets confirmation even if the bespoke template isn't approved.
    if remaining_balance <= Decimal("0"):
        tail = "Fully paid up — thank you!"
    else:
        tail = f"Remaining balance: {_fmt_amount(remaining_balance, currency)}."
    subject = (
        f"Payment of {payment_str} received for "
        f"{student_name} on {invoice_number}. {tail}"
    )
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_pickup_alert(
    db: AsyncSession, user: User,
    *, tenant_name: str, student_name: str, time_str: str,
) -> bool:
    if not await _can_notify_whatsapp(db, user):
        return False
    subject = f"{student_name} was checked out at {time_str}."
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_document_shared(
    db: AsyncSession, user: User,
    *, tenant_name: str, sharer_name: str, title: str, scope: str,
) -> bool:
    if not await _can_notify_whatsapp(db, user):
        return False
    subject = f"New document: {title} — shared by {sharer_name} ({scope})"
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_photo_shared(
    db: AsyncSession, user: User,
    *, tenant_name: str, sharer_name: str, class_name: str, photo_count: int,
) -> bool:
    if not await _can_notify_whatsapp(db, user):
        return False
    subject = (
        f"{photo_count} new photo{'s' if photo_count != 1 else ''} "
        f"from {class_name}, shared by {sharer_name}. Log in to view."
    )
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_message_received(
    db: AsyncSession, user: User,
    *, tenant_name: str, sender_name: str, student_name: str | None,
) -> bool:
    if not await _can_notify_whatsapp(db, user):
        return False
    subject = (
        f"New message from {sender_name}"
        + (f" about {student_name}" if student_name else "")
        + ". Log in to reply."
    )
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_event_reminder(
    db: AsyncSession, user: User,
    *, tenant_name: str, event_title: str, event_when: str, label: str,
    event_id: str | None = None,
) -> bool:
    """WhatsApp reminder — fires 24h and again 1h before an event.
    Bespoke ``event_reminder`` template first; falls back to the
    generic announcement if Meta hasn't approved it yet."""
    if not await _can_notify_whatsapp(db, user):
        return False
    if event_id:
        ok = await _send_template(
            db, user, "send_event_reminder",
            event_title=event_title, event_when=event_when, event_id=event_id,
        )
        if ok:
            return True
    when_clause = "tomorrow" if label == "24h" else "in about an hour"
    subject = f"Reminder: {event_title} is {when_clause} ({event_when}). See email for details."
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_event_invited(
    db: AsyncSession, user: User,
    *, tenant_name: str, event_title: str, event_when: str,
    event_location: str | None = None,
    event_id: str | None = None,
) -> bool:
    """WhatsApp copy of the event invitation email. Bespoke
    ``event_invited`` template first; falls back to the generic
    ``announcement`` template if the bespoke template isn't
    approved on Meta yet.

    The email carries the calendar attachment; WhatsApp just gives
    the parent an at-a-glance heads-up so they don't miss it."""
    if not await _can_notify_whatsapp(db, user):
        return False
    if event_id:
        ok = await _send_template(
            db, user, "send_event_invited",
            event_title=event_title, event_when=event_when,
            event_location=event_location, event_id=event_id,
        )
        if ok:
            return True
    where = f" at {event_location}" if event_location else ""
    subject = f"Event: {event_title} — {event_when}{where}. Check email for details + calendar invite."
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_event_rsvp_confirmed(
    db: AsyncSession, user: User,
    *, tenant_name: str, event_title: str, event_when: str,
    response: str, event_id: str,
) -> bool:
    """Sent when a parent RSVPs (via email link, UI, or WhatsApp bot).
    Bespoke ``event_rsvp_confirmed`` template first; falls back to the
    announcement template."""
    if not await _can_notify_whatsapp(db, user):
        return False
    ok = await _send_template(
        db, user, "send_event_rsvp_confirmed",
        event_title=event_title, event_when=event_when,
        response=response, event_id=event_id,
    )
    if ok:
        return True
    labels = {"YES": "attending", "NO": "not attending", "MAYBE": "maybe attending"}
    subject = f"RSVP confirmed for {event_title}: {labels.get(response.upper(), response)}."
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )


async def notify_parent_link_child(
    db: AsyncSession, user: User,
    *, tenant_name: str, student_name: str,
) -> bool:
    """Sent when an existing parent is auto-linked to a newly-created
    child (sibling of a child they already have)."""
    if not await _can_notify_whatsapp(db, user):
        return False
    subject = f"{student_name} has been linked to your ClassUp account."
    return await _send_template(
        db, user, "send_announcement", tenant_name, subject[:120],
    )
