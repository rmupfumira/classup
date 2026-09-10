"""Tests for the orphaned-parent + orphaned-invitation cleanup when a
student is soft-deleted or unlinked.

Compliance win: POPIA / GDPR say we should stop processing a family's
data once the reason we had it (their child at this school) is gone.
So when a student leaves:
  - Any parent whose ONLY child at this tenant was that student gets
    their account soft-deleted (deleted_at + is_active=False).
  - Parents with siblings still enrolled are UNTOUCHED.
  - Any PENDING parent_invitations for the deleted student are
    marked EXPIRED so a stale link can't create a link to a
    tombstoned student.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import (
    InvitationStatus, ParentInvitation, ParentStudent, Student, User,
)
from app.models.user import Role
from app.services.student_service import get_student_service
from app.utils.security import hash_password


async def _make_student(db, tenant_id, name="Aaron"):
    s = Student(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        first_name=name,
        last_name="Test",
        date_of_birth=date(2018, 1, 1),
        is_active=True,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_parent(db, tenant_id, email=None):
    p = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=email or f"parent-{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password("x"),
        first_name="Parent",
        last_name="Test",
        role=Role.PARENT.value,
        is_active=True,
        language="en",
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _link(db, parent, student):
    link = ParentStudent(
        id=uuid.uuid4(),
        parent_id=parent.id,
        student_id=student.id,
        relationship_type="PARENT",
        is_primary=True,
    )
    db.add(link)
    await db.commit()
    return link


class TestOrphanedParentCleanup:
    async def test_parent_with_only_this_child_is_soft_deleted(
        self, db, test_tenant, test_admin,
    ):
        """The core case: a parent whose only child leaves loses their
        account. Historical join stays for audit."""
        student = await _make_student(db, test_tenant.id)
        parent = await _make_parent(db, test_tenant.id)
        await _link(db, parent, student)

        await get_student_service().delete_student(db, student.id)
        await db.commit()

        # Parent should now be soft-deleted
        refreshed = await db.get(User, parent.id)
        assert refreshed.deleted_at is not None, (
            "Parent with no remaining children should be soft-deleted"
        )
        assert refreshed.is_active is False

    async def test_parent_with_sibling_still_enrolled_is_untouched(
        self, db, test_tenant, test_admin,
    ):
        """A parent with two kids — only one leaves. The parent must
        stay active. This is the sibling case the whole rule was
        designed around."""
        student_a = await _make_student(db, test_tenant.id, name="Aaron")
        student_b = await _make_student(db, test_tenant.id, name="Bianca")
        parent = await _make_parent(db, test_tenant.id)
        await _link(db, parent, student_a)
        await _link(db, parent, student_b)

        await get_student_service().delete_student(db, student_a.id)
        await db.commit()

        refreshed = await db.get(User, parent.id)
        assert refreshed.deleted_at is None
        assert refreshed.is_active is True

    async def test_shared_student_only_deletes_orphaned_parent(
        self, db, test_tenant, test_admin,
    ):
        """Two parents on one student. Parent A also has a sibling
        (should stay), Parent B has only this one (should be deleted).
        Guards against the mistake of soft-deleting BOTH parents
        because the student is gone."""
        student_a = await _make_student(db, test_tenant.id, name="Aaron")
        sibling = await _make_student(db, test_tenant.id, name="Bianca")
        parent_a = await _make_parent(db, test_tenant.id)  # has sibling too
        parent_b = await _make_parent(db, test_tenant.id)  # only this student
        await _link(db, parent_a, student_a)
        await _link(db, parent_a, sibling)
        await _link(db, parent_b, student_a)

        await get_student_service().delete_student(db, student_a.id)
        await db.commit()

        assert (await db.get(User, parent_a.id)).deleted_at is None
        assert (await db.get(User, parent_b.id)).deleted_at is not None

    async def test_pending_invitations_are_expired(
        self, db, test_tenant, test_admin,
    ):
        """A pending invitation for a student who's just been deleted
        must be marked EXPIRED so it can't be redeemed."""
        student = await _make_student(db, test_tenant.id)
        invite = ParentInvitation(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            student_id=student.id,
            email="dad@test.local",
            first_name="Dad",
            last_name="Test",
            created_by=test_admin.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(invite)
        await db.commit()

        await get_student_service().delete_student(db, student.id)
        await db.commit()

        refreshed = await db.get(ParentInvitation, invite.id)
        assert refreshed.status == InvitationStatus.EXPIRED.value

    async def test_already_accepted_invitation_is_untouched(
        self, db, test_tenant, test_admin,
    ):
        """ACCEPTED invitations are historical records — must not be
        changed to EXPIRED when the student is deleted (only PENDING
        ones matter for the "can't redeem" concern)."""
        student = await _make_student(db, test_tenant.id)
        invite = ParentInvitation(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            student_id=student.id,
            email="mum@test.local",
            first_name="Mum",
            last_name="Test",
            created_by=test_admin.id,
            status=InvitationStatus.ACCEPTED.value,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(invite)
        await db.commit()

        await get_student_service().delete_student(db, student.id)
        await db.commit()

        refreshed = await db.get(ParentInvitation, invite.id)
        assert refreshed.status == InvitationStatus.ACCEPTED.value

    async def test_unlink_parent_soft_deletes_when_last_child(
        self, db, test_tenant, test_admin,
    ):
        """Unlink flow (not student-delete) applies the same rule."""
        student = await _make_student(db, test_tenant.id)
        parent = await _make_parent(db, test_tenant.id)
        await _link(db, parent, student)

        await get_student_service().unlink_parent(db, student.id, parent.id)
        await db.commit()

        refreshed = await db.get(User, parent.id)
        assert refreshed.deleted_at is not None

    async def test_helper_refuses_to_touch_non_parent_users(
        self, db, test_tenant, test_admin,
    ):
        """Defensive: if a data bug ever creates a ParentStudent row
        for a non-parent user, the cleanup logs but doesn't
        soft-delete them (staff account safety)."""
        student = await _make_student(db, test_tenant.id)
        # test_admin is SCHOOL_ADMIN — we simulate an inconsistent
        # link by adding a ParentStudent row for them, then delete
        # the student. Nothing should happen to the admin account.
        db.add(ParentStudent(
            id=uuid.uuid4(),
            parent_id=test_admin.id,
            student_id=student.id,
            relationship_type="PARENT",
            is_primary=False,
        ))
        await db.commit()

        await get_student_service().delete_student(db, student.id)
        await db.commit()

        assert (await db.get(User, test_admin.id)).deleted_at is None
        assert (await db.get(User, test_admin.id)).is_active is True
