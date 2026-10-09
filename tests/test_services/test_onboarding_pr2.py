"""PR 2 — admin form polish + per-channel email preferences.

Covers:
- ParentEnrollmentInfo rejects "WhatsApp invite ticked, no phone" at
  the schema layer so the admin can't send an invite with nothing to
  WhatsApp to.
- parent_notifier.can_notify_email honours User.email_opted_in.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models import User
from app.models.user import Role
from app.schemas.student import ParentEnrollmentInfo
from app.services import parent_notifier


# ---------------------------------------------------------------------------
# 1. Admin form: WhatsApp invite requires phone
# ---------------------------------------------------------------------------

class TestParentEnrollmentPhoneGate:
    def test_whatsapp_invite_requires_phone(self):
        with pytest.raises(ValidationError) as exc:
            ParentEnrollmentInfo(
                first_name="Pat",
                last_name="Parent",
                email="pat@example.com",
                phone=None,
                send_whatsapp_invite=True,
            )
        assert "Mobile number is required" in str(exc.value)

    def test_whatsapp_invite_with_phone_ok(self):
        info = ParentEnrollmentInfo(
            first_name="Pat",
            last_name="Parent",
            email="pat@example.com",
            phone="+263771234567",
            send_whatsapp_invite=True,
        )
        assert info.phone == "+263771234567"

    def test_no_whatsapp_invite_allows_blank_phone(self):
        """If the admin isn't sending a WhatsApp invite, phone can be
        skipped — the parent will fill it at registration."""
        info = ParentEnrollmentInfo(
            first_name="Pat",
            last_name="Parent",
            email="pat@example.com",
            phone=None,
            send_whatsapp_invite=False,
        )
        assert info.phone is None

    def test_grandparent_relationship_accepted(self):
        """Dropdown gained a GRANDPARENT option in the UI; the schema
        already accepted any string, verify."""
        info = ParentEnrollmentInfo(
            first_name="Granny",
            last_name="One",
            email="granny@example.com",
            phone=None,
            relationship_type="GRANDPARENT",
            send_whatsapp_invite=False,
        )
        assert info.relationship_type == "GRANDPARENT"


# ---------------------------------------------------------------------------
# 2. Email channel gate
# ---------------------------------------------------------------------------

class TestEmailGate:
    def _user(self, **overrides) -> User:
        base = dict(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            email="parent@example.com",
            password_hash="x",
            first_name="Pat",
            last_name="Parent",
            role=Role.PARENT.value,
            is_active=True,
            whatsapp_opted_in=False,
            email_opted_in=True,
        )
        base.update(overrides)
        return User(**base)

    def test_opted_in_user_passes(self):
        assert parent_notifier.can_notify_email(self._user()) is True

    def test_opted_out_user_blocked(self):
        assert parent_notifier.can_notify_email(
            self._user(email_opted_in=False)
        ) is False

    def test_user_without_email_blocked(self):
        """An email with no address nowhere to send."""
        u = self._user()
        u.email = ""
        assert parent_notifier.can_notify_email(u) is False

    def test_inactive_user_blocked(self):
        assert parent_notifier.can_notify_email(
            self._user(is_active=False)
        ) is False

    def test_none_user_blocked(self):
        assert parent_notifier.can_notify_email(None) is False
