"""Regression tests for the parent → admin reply guard.

Tester bug (2026-10-09): Admin sent a message to a parent about the
parent's child. Parent hit Reply and typed a response. The server
rejected it with "You have no authority to perform this action"
because MessageService._validate_can_message only allowed parents
to message TEACHERS of the student's class — admin wasn't in that
set, so replies to admin threads died.

Fix: allow a parent to reply to any SCHOOL_ADMIN / SUPER_ADMIN on
the same tenant as well. Teacher-of-class check is preserved so
cross-child or cross-class fishing stays blocked.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ForbiddenException
from app.models import ParentStudent, SchoolClass, Student, Tenant, User
from app.models.user import Role
from app.services.message_service import get_message_service
from app.utils.security import hash_password


@pytest_asyncio.fixture
async def tenant_setup(db: AsyncSession):
    """Admin + parent + student + a different-class teacher.

    - admin: SCHOOL_ADMIN on the tenant.
    - parent: PARENT linked to `student`.
    - student: in `class_a`.
    - class_a: student's class.
    - teacher_other: TEACHER but on a DIFFERENT class (class_b), so
      cross-class check still works.
    """
    tid = uuid.uuid4()
    tenant = Tenant(
        id=tid,
        name=f"Reply {tid.hex[:6]}",
        slug=f"reply-{tid.hex[:8]}",
        email=f"admin@{tid.hex[:6]}.example.com",
        education_type="PRIMARY_SCHOOL",
        settings={},
        is_active=True,
        onboarding_completed=True,
    )
    db.add(tenant)
    await db.flush()

    admin = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"admin-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Head", last_name="Teacher",
        role=Role.SCHOOL_ADMIN.value, is_active=True,
    )
    parent = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"parent-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Mum", last_name="Parent",
        role=Role.PARENT.value, is_active=True,
    )
    teacher_other = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"other-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Not", last_name="MyTeacher",
        role=Role.TEACHER.value, is_active=True,
    )
    class_a = SchoolClass(
        id=uuid.uuid4(), tenant_id=tid, name="Grade 7A", is_active=True,
    )
    student = Student(
        id=uuid.uuid4(), tenant_id=tid,
        first_name="Kid", last_name="Student",
        class_id=class_a.id, is_active=True,
    )
    link = ParentStudent(
        id=uuid.uuid4(),
        parent_id=parent.id, student_id=student.id,
        relationship_type="PARENT", is_primary=True,
    )
    db.add_all([admin, parent, teacher_other, class_a, student, link])
    await db.commit()

    yield {
        "tid": tid, "admin": admin, "parent": parent,
        "teacher_other": teacher_other,
        "student": student, "class_a": class_a,
    }

    from sqlalchemy import text as _text
    async with db.bind.connect() as conn:
        await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": tid})
        await conn.commit()


@pytest.mark.asyncio
async def test_parent_can_reply_to_school_admin(db: AsyncSession, tenant_setup):
    """The exact production path: parent replies to a thread an
    admin started about their child. Must not raise."""
    svc = get_message_service()
    from app.utils.tenant_context import _tenant_id

    tok = _tenant_id.set(tenant_setup["tid"])
    try:
        await svc._validate_can_message(
            db,
            sender_id=tenant_setup["parent"].id,
            sender_role="PARENT",
            student_id=tenant_setup["student"].id,
            recipient_id=tenant_setup["admin"].id,
        )
    finally:
        _tenant_id.reset(tok)


@pytest.mark.asyncio
async def test_parent_cannot_reply_to_teacher_of_a_different_class(
    db: AsyncSession, tenant_setup,
):
    """Fishing guard preserved: a teacher who does NOT teach the
    student's class is still off-limits (even though they're a staff
    member on this tenant)."""
    svc = get_message_service()
    from app.utils.tenant_context import _tenant_id

    tok = _tenant_id.set(tenant_setup["tid"])
    try:
        with pytest.raises(ForbiddenException):
            await svc._validate_can_message(
                db,
                sender_id=tenant_setup["parent"].id,
                sender_role="PARENT",
                student_id=tenant_setup["student"].id,
                recipient_id=tenant_setup["teacher_other"].id,
            )
    finally:
        _tenant_id.reset(tok)


@pytest.mark.asyncio
async def test_parent_cannot_message_about_someone_elses_child(
    db: AsyncSession, tenant_setup,
):
    """Cross-child guard preserved: the parent can message admin
    about THEIR OWN child but not about a child that isn't theirs."""
    # Create a second student who is NOT linked to the parent.
    other_student = Student(
        id=uuid.uuid4(), tenant_id=tenant_setup["tid"],
        first_name="Someone", last_name="Else",
        class_id=tenant_setup["class_a"].id, is_active=True,
    )
    db.add(other_student)
    await db.commit()

    svc = get_message_service()
    from app.utils.tenant_context import _tenant_id

    tok = _tenant_id.set(tenant_setup["tid"])
    try:
        with pytest.raises(ForbiddenException):
            await svc._validate_can_message(
                db,
                sender_id=tenant_setup["parent"].id,
                sender_role="PARENT",
                student_id=other_student.id,
                recipient_id=tenant_setup["admin"].id,
            )
    finally:
        _tenant_id.reset(tok)


@pytest.mark.asyncio
async def test_parent_cannot_reply_to_admin_of_a_different_tenant(
    db: AsyncSession, tenant_setup,
):
    """Cross-tenant guard preserved: an admin on ANOTHER tenant
    isn't a valid recipient even though they're a SCHOOL_ADMIN."""
    other_tid = uuid.uuid4()
    other_tenant = Tenant(
        id=other_tid,
        name=f"Other {other_tid.hex[:6]}",
        slug=f"other-{other_tid.hex[:8]}",
        email=f"admin@{other_tid.hex[:6]}.example.com",
        education_type="PRIMARY_SCHOOL",
        settings={}, is_active=True, onboarding_completed=True,
    )
    foreign_admin = User(
        id=uuid.uuid4(), tenant_id=other_tid,
        email=f"foreign-{other_tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Foreign", last_name="Admin",
        role=Role.SCHOOL_ADMIN.value, is_active=True,
    )
    db.add_all([other_tenant, foreign_admin])
    await db.commit()

    svc = get_message_service()
    from app.utils.tenant_context import _tenant_id

    tok = _tenant_id.set(tenant_setup["tid"])
    try:
        with pytest.raises(ForbiddenException):
            await svc._validate_can_message(
                db,
                sender_id=tenant_setup["parent"].id,
                sender_role="PARENT",
                student_id=tenant_setup["student"].id,
                recipient_id=foreign_admin.id,
            )
    finally:
        _tenant_id.reset(tok)
        from sqlalchemy import text as _text
        async with db.bind.connect() as conn:
            await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": other_tid})
            await conn.commit()
