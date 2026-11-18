"""Tests for 2026-11-18 parent-notification correctness fixes.

Covers:
  - attendance_service._notify_parents_attendance only emails/WhatsApps
    for exceptional statuses (ABSENT / LATE / EXCUSED). PRESENT stays
    in-app only.
  - announcement_service._send_notifications only emails/WhatsApps for
    URGENT / EMERGENCY severities (or when the tenant opts in to
    low-severity external channels).
  - parent_notifier.notify_parent_invite respects tenant WhatsApp
    feature when tenant_id is supplied.

Each assertion uses patched callables to verify which channels fired.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _AttendanceStudent:
    """Plain stand-in for Student + relationships used by the notifier."""

    def __init__(self, tenant_id, parent):
        self.id = uuid.uuid4()
        self.tenant_id = tenant_id
        self.full_name = "Sam Smith"
        self.parent_students = [SimpleNamespace(parent=parent)]


@pytest.mark.asyncio
async def test_attendance_present_skips_external_channels():
    """A PRESENT mark fires in-app but NOT email or WhatsApp. Previously
    the notifier blasted all three channels on every roll-call."""
    from app.services.attendance_service import AttendanceService

    parent = SimpleNamespace(
        id=uuid.uuid4(), is_active=True, first_name="Ada",
        email="ada@example.com", email_opted_in=True,
        deleted_at=None,
    )
    tenant_id = uuid.uuid4()
    student = _AttendanceStudent(tenant_id, parent)

    svc = AttendanceService()

    tenant = SimpleNamespace(name="Demo School")
    with patch("app.services.email_service.get_email_service") as get_email, \
         patch("app.services.notification_service.get_notification_service") as get_notif, \
         patch("app.services.parent_notifier.notify_attendance_alert", new_callable=AsyncMock) as notify_wa, \
         patch("app.services.attendance_service.AsyncSession", MagicMock()):
        email_mock = MagicMock()
        email_mock.send_attendance_alert = AsyncMock()
        get_email.return_value = email_mock
        notif_mock = MagicMock()
        notif_mock.notify_attendance_marked = AsyncMock()
        get_notif.return_value = notif_mock

        db = AsyncMock()
        db.get = AsyncMock(return_value=tenant)

        await svc._notify_parents_attendance(
            db, student, "PRESENT", date(2026, 11, 18), notes=None,
        )

        email_mock.send_attendance_alert.assert_not_called()
        notify_wa.assert_not_called()
        # In-app still fires
        notif_mock.notify_attendance_marked.assert_called_once()


@pytest.mark.asyncio
async def test_attendance_absent_fires_external_channels():
    """ABSENT must still fire email + WhatsApp — the spam fix must not
    starve the exceptional status that the parent actually wants."""
    from app.services.attendance_service import AttendanceService

    parent = SimpleNamespace(
        id=uuid.uuid4(), is_active=True, first_name="Ada",
        email="ada@example.com", email_opted_in=True,
        deleted_at=None,
    )
    student = _AttendanceStudent(uuid.uuid4(), parent)
    svc = AttendanceService()

    tenant = SimpleNamespace(name="Demo School")
    with patch("app.services.email_service.get_email_service") as get_email, \
         patch("app.services.notification_service.get_notification_service") as get_notif, \
         patch("app.services.parent_notifier.notify_attendance_alert", new_callable=AsyncMock) as notify_wa:
        email_mock = MagicMock()
        email_mock.send_attendance_alert = AsyncMock()
        get_email.return_value = email_mock
        notif_mock = MagicMock()
        notif_mock.notify_attendance_marked = AsyncMock()
        get_notif.return_value = notif_mock

        db = AsyncMock()
        db.get = AsyncMock(return_value=tenant)

        await svc._notify_parents_attendance(
            db, student, "ABSENT", date(2026, 11, 18), notes=None,
        )

        email_mock.send_attendance_alert.assert_called_once()
        notify_wa.assert_called_once()


@pytest.mark.asyncio
async def test_announcement_info_skips_external_channels():
    """INFO announcements stay in-app only. Previously everything got
    email + WhatsApp blasted."""
    from app.services.announcement_service import AnnouncementService

    announcement = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        severity="INFO", title="Picture day",
        body="Smile!", school_class=None, creator=None,
    )
    svc = AnnouncementService()

    with patch.object(svc, "_get_recipient_user_ids", new_callable=AsyncMock) as get_ids, \
         patch("app.services.notification_service.get_notification_service") as get_notif, \
         patch("app.services.email_service.get_email_service") as get_email:
        get_ids.return_value = [uuid.uuid4(), uuid.uuid4()]
        notif_mock = MagicMock()
        notif_mock.create_bulk_notifications = AsyncMock()
        get_notif.return_value = notif_mock
        email_mock = MagicMock()
        email_mock.send = AsyncMock()
        get_email.return_value = email_mock

        tenant = SimpleNamespace(name="Demo", settings={})
        db = AsyncMock()
        db.get = AsyncMock(return_value=tenant)

        await svc._send_notifications(db, announcement)

        # In-app fired
        notif_mock.create_bulk_notifications.assert_called_once()
        # External did NOT fire — the method short-circuited before reaching
        # get_email_service().
        email_mock.send.assert_not_called()


@pytest.mark.asyncio
async def test_announcement_urgent_fires_external_channels():
    """URGENT must still fire email. The severity gate must not starve
    the exceptional severities."""
    from app.services.announcement_service import AnnouncementService

    announcement = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        severity="URGENT", title="Water outage",
        body="School closed today", school_class=None, creator=None,
    )
    svc = AnnouncementService()

    tenant = SimpleNamespace(name="Demo", settings={})
    parent = SimpleNamespace(
        id=uuid.uuid4(), is_active=True, email="p@example.com",
        email_opted_in=True, first_name="Ada", deleted_at=None,
    )

    async def _exec(_stmt):
        result = MagicMock()
        result.scalars.return_value.all.return_value = [parent]
        return result

    with patch.object(svc, "_get_recipient_user_ids", new_callable=AsyncMock) as get_ids, \
         patch("app.services.notification_service.get_notification_service") as get_notif, \
         patch("app.services.email_service.get_email_service") as get_email, \
         patch("app.services.parent_notifier.notify_announcement", new_callable=AsyncMock) as notify_wa:
        get_ids.return_value = [parent.id]
        notif_mock = MagicMock()
        notif_mock.create_bulk_notifications = AsyncMock()
        get_notif.return_value = notif_mock
        email_mock = MagicMock()
        email_mock.send = AsyncMock()
        get_email.return_value = email_mock

        db = AsyncMock()
        db.get = AsyncMock(return_value=tenant)
        db.execute = AsyncMock(side_effect=_exec)

        await svc._send_notifications(db, announcement)

        email_mock.send.assert_called_once()
        notify_wa.assert_called_once()


def test_can_notify_email_respects_event_pref():
    """A parent who turned off invoice emails must not get an invoice
    email even if their master email_opted_in is True (2026-11-18)."""
    from types import SimpleNamespace
    from app.services import parent_notifier

    parent = SimpleNamespace(
        id="p1", is_active=True, deleted_at=None,
        email="p@example.com", email_opted_in=True,
        notification_prefs={"invoices": {"email": False}},
    )
    # Master master toggle on + per-event on → fire.
    assert parent_notifier.can_notify_email(parent, event="announcements") is True
    # Master on + per-event off → skip.
    assert parent_notifier.can_notify_email(parent, event="invoices") is False
    # Event not in prefs dict → default on.
    assert parent_notifier.can_notify_email(parent, event="events") is True


def test_event_channel_on_defaults_true():
    """Missing event or missing channel → treated as opted-in (new
    users are fully subscribed until they turn something off)."""
    from types import SimpleNamespace
    from app.services import parent_notifier

    p_empty = SimpleNamespace(notification_prefs={})
    assert parent_notifier._event_channel_on(p_empty, "attendance", "email") is True

    p_partial = SimpleNamespace(
        notification_prefs={"attendance": {"email": True}},
    )
    # WhatsApp channel missing → default on.
    assert parent_notifier._event_channel_on(p_partial, "attendance", "whatsapp") is True


@pytest.mark.asyncio
async def test_parent_invite_skipped_when_tenant_whatsapp_off():
    """Previously fired on any configured tenant; now gates on
    tenant.settings.features.whatsapp_enabled when tenant_id supplied."""
    from app.services import parent_notifier

    svc_mock = MagicMock()
    svc_mock.is_configured = True
    svc_mock.send_parent_invite = AsyncMock(return_value={"id": "x"})

    tenant = SimpleNamespace(
        settings={"features": {"whatsapp_enabled": False}},
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=tenant)

    with patch(
        "app.services.parent_notifier.get_whatsapp_service_from_db",
        new_callable=AsyncMock,
        return_value=svc_mock,
    ):
        result = await parent_notifier.notify_parent_invite(
            db, None, to_phone="+27123", tenant_name="Demo", code="ABC",
            tenant_id=uuid.uuid4(),
        )

    assert result is False
    svc_mock.send_parent_invite.assert_not_called()
