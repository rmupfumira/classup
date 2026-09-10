"""Tests for capturing parents at student-enrollment time.

The change: ``StudentCreate`` now takes a ``parents`` list, and the
service fans out per parent — linking an existing PARENT user if
one exists on the tenant with that email, otherwise creating a
ParentInvitation and sending the invite email + (best-effort)
WhatsApp signup nudge. Sibling flow also notifies the linked
parent that a new child was added to their profile.

Tests here focus on the fan-out branches and the fallback shape
of the ``parent_results`` list — the UI relies on those statuses.
Email + WhatsApp sends are patched out so no real send is attempted.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import ParentInvitation, ParentStudent, User
from app.models.user import Role
from app.schemas.student import ParentEnrollmentInfo, StudentCreate
from app.services.student_service import get_student_service
from app.utils.security import hash_password


def _make_create_payload(
    first="Aaron", last="Test", parents=None,
) -> StudentCreate:
    return StudentCreate(
        first_name=first,
        last_name=last,
        date_of_birth=date(2018, 1, 1),
        parents=parents or [],
    )


class TestCreateStudentWithoutParents:
    """The zero-parents path — has to keep working for admins who
    genuinely don't have parent contact yet (walk-in registrations,
    the sibling-inheritance flow, ward-of-court cases)."""

    async def test_no_parents_saves_student_returns_empty_results(
        self, db, test_tenant, test_admin,
    ):
        service = get_student_service()
        student, parent_results = await service.create_student(
            db, _make_create_payload(),
        )
        await db.commit()

        assert student.id is not None
        assert student.tenant_id == test_tenant.id
        assert parent_results == []


class TestCreateStudentInvitesNewParent:
    """The default enrollment case — admin types a fresh parent
    email that has no User account. Service creates a
    ParentInvitation and reports status=invited."""

    async def test_new_email_creates_invitation(
        self, db, test_tenant, test_admin,
    ):
        with (
            patch(
                "app.services.email_service.EmailService.send_parent_invitation",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.parent_notifier.notify_parent_invite",
                new=AsyncMock(return_value=False),
            ),
        ):
            service = get_student_service()
            student, results = await service.create_student(
                db,
                _make_create_payload(parents=[
                    ParentEnrollmentInfo(
                        first_name="Nomsa",
                        last_name="Moyo",
                        email="Nomsa@Example.com",
                        phone="+27821234567",
                        send_whatsapp_invite=True,
                    ),
                ]),
            )
            await db.commit()

        assert len(results) == 1
        assert results[0]["status"] == "invited"
        assert results[0]["email"] == "nomsa@example.com"

        stmt = select(ParentInvitation).where(
            ParentInvitation.student_id == student.id,
        )
        invitations = (await db.execute(stmt)).scalars().all()
        assert len(invitations) == 1
        assert invitations[0].email == "nomsa@example.com"

    async def test_no_phone_still_sends_email_invite(
        self, db, test_tenant, test_admin,
    ):
        """Phone is optional — email invite alone is a valid outcome."""
        wa_mock = AsyncMock(return_value=False)
        with (
            patch(
                "app.services.email_service.EmailService.send_parent_invitation",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.parent_notifier.notify_parent_invite",
                new=wa_mock,
            ),
        ):
            service = get_student_service()
            _, results = await service.create_student(
                db,
                _make_create_payload(parents=[
                    ParentEnrollmentInfo(
                        first_name="Nomsa",
                        last_name="Moyo",
                        email="nomsa@example.com",
                        phone=None,
                        send_whatsapp_invite=True,
                    ),
                ]),
            )
            await db.commit()

        assert results[0]["status"] == "invited"
        # WhatsApp is skipped when no phone was captured, even if the
        # admin ticked the box — nothing to send it to.
        wa_mock.assert_not_awaited()


class TestCreateStudentLinksExistingParent:
    """Sibling case at create time — the admin types the email of
    a parent who already has an account on this tenant. No invite,
    just link + notify."""

    async def test_existing_parent_gets_linked_and_notified(
        self, db, test_tenant, test_admin,
    ):
        parent = User(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            email="dad@example.com",
            password_hash=hash_password("x"),
            first_name="Dad",
            last_name="Test",
            role=Role.PARENT.value,
            is_active=True,
            language="en",
        )
        db.add(parent)
        await db.commit()

        email_send = AsyncMock(return_value=True)
        wa_notify = AsyncMock(return_value=True)
        with (
            patch("app.services.email_service.EmailService.send", new=email_send),
            patch(
                "app.services.parent_notifier.notify_parent_link_child",
                new=wa_notify,
            ),
        ):
            service = get_student_service()
            student, results = await service.create_student(
                db,
                _make_create_payload(parents=[
                    ParentEnrollmentInfo(
                        first_name="Dad",
                        last_name="Test",
                        email="dad@example.com",
                        phone="+27827654321",
                        send_whatsapp_invite=True,
                    ),
                ]),
            )
            await db.commit()

        assert results[0]["status"] == "linked"

        # Link row exists.
        link_stmt = select(ParentStudent).where(
            ParentStudent.parent_id == parent.id,
            ParentStudent.student_id == student.id,
        )
        assert (await db.execute(link_stmt)).scalar_one_or_none() is not None

        # Existing parent's WhatsApp opt-in gets flipped ON and their
        # phone captured — this matches the "admin already knew the
        # parent wants WhatsApp" framing.
        await db.refresh(parent)
        assert parent.whatsapp_opted_in is True
        assert parent.whatsapp_phone == "+27827654321"

        # Both notifications fired.
        email_send.assert_awaited_once()
        wa_notify.assert_awaited_once()


class TestParentFailureDoesNotBlockStudent:
    """A per-parent failure must not roll back the student — the admin
    still gets a student row and a clear error to retry."""

    async def test_invitation_ValueError_returned_as_error_status(
        self, db, test_tenant, test_admin,
    ):
        # The invitation service raises ValueError when a duplicate
        # PENDING invitation already exists — simulate by patching.
        with (
            patch(
                "app.services.invitation_service.InvitationService.create_invitation",
                new=AsyncMock(side_effect=ValueError("dup invite")),
            ),
        ):
            service = get_student_service()
            student, results = await service.create_student(
                db,
                _make_create_payload(parents=[
                    ParentEnrollmentInfo(
                        first_name="Nomsa",
                        last_name="Moyo",
                        email="nomsa@example.com",
                    ),
                ]),
            )
            await db.commit()

        # Student was still saved.
        assert student.id is not None
        # Per-parent error surfaces cleanly.
        assert results[0]["status"] == "error"
        assert "dup invite" in results[0]["message"]


class TestLinkParentNotifies:
    """The sibling flow at create time uses POST /students/{id}/parents
    for parents inherited from an already-picked sibling. That endpoint
    calls ``StudentService.link_parent`` — it must fire the "new child
    added" email + WhatsApp so parents don't get a silent link."""

    async def test_link_parent_fires_notifications(
        self, db, test_tenant, test_admin,
    ):
        from app.schemas.student import LinkParentRequest

        parent = User(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            email="mum@example.com",
            password_hash=hash_password("x"),
            first_name="Mum", last_name="Test",
            role=Role.PARENT.value, is_active=True, language="en",
        )
        db.add(parent)
        # Existing student we're linking to.
        service = get_student_service()
        student, _ = await service.create_student(db, _make_create_payload())
        await db.commit()

        email_send = AsyncMock(return_value=True)
        wa_notify = AsyncMock(return_value=True)
        with (
            patch("app.services.email_service.EmailService.send", new=email_send),
            patch(
                "app.services.parent_notifier.notify_parent_link_child",
                new=wa_notify,
            ),
        ):
            await service.link_parent(
                db, student.id,
                LinkParentRequest(parent_id=parent.id, is_primary=True),
            )
            await db.commit()

        email_send.assert_awaited_once()
        wa_notify.assert_awaited_once()
