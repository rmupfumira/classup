"""Teacher class + subject assignment tests.

Two surfaces exercised here:
1. ``TeacherInvitationService.create_invitation`` + ``accept_invitation``
   with the new ``intended_assignments`` plan — invites remember the
   admin's pre-plan and apply it at acceptance (idempotently).
2. ``ClassService.add_general_assignment`` /
   ``remove_general_assignment`` / ``add_subject_assignment`` /
   ``remove_subject_assignment`` — the per-teacher editing surface the
   Teachers list page uses after acceptance.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException
from app.models import (
    ClassSubject,
    ClassSubjectTeacher,
    SchoolClass,
    Subject,
    TeacherClass,
    Tenant,
    User,
)
from app.models.teacher_invitation import TeacherInvitation
from app.models.user import Role
from app.services.class_service import get_class_service
from app.services.teacher_invitation_service import (
    get_teacher_invitation_service,
)
from app.utils.security import hash_password
from app.utils.tenant_context import (
    _current_user_id,
    _current_user_role,
    _tenant_id,
)


@pytest_asyncio.fixture
async def world(db: AsyncSession):
    """Isolated world: tenant with subject_teacher_enabled=True, one
    admin, one class with Maths + English attached, and a second class
    (empty) for cross-class scenarios.
    """
    tid = uuid.uuid4()
    tenant = Tenant(
        id=tid, name=f"TA {tid.hex[:6]}",
        slug=f"ta-{tid.hex[:8]}",
        email=f"admin@{tid.hex[:6]}.example.com",
        education_type="HIGH_SCHOOL",
        settings={"subject_teacher_enabled": True},
        is_active=True, onboarding_completed=True,
    )
    admin = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"admin-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Head", last_name="Admin",
        role=Role.SCHOOL_ADMIN.value, is_active=True,
    )
    cls_a = SchoolClass(
        id=uuid.uuid4(), tenant_id=tid, name="Form 4A", is_active=True,
    )
    cls_b = SchoolClass(
        id=uuid.uuid4(), tenant_id=tid, name="Form 4B", is_active=True,
    )
    maths = Subject(
        id=uuid.uuid4(), tenant_id=tid,
        name="Mathematics", code=f"MATH-{tid.hex[:4]}",
        default_total_marks=100, is_active=True,
    )
    english = Subject(
        id=uuid.uuid4(), tenant_id=tid,
        name="English", code=f"ENG-{tid.hex[:4]}",
        default_total_marks=100, is_active=True,
    )
    db.add_all([tenant, admin, cls_a, cls_b, maths, english])
    await db.flush()
    db.add_all([
        ClassSubject(
            id=uuid.uuid4(), class_id=cls_a.id, subject_id=maths.id,
            is_compulsory=True, display_order=0,
        ),
        ClassSubject(
            id=uuid.uuid4(), class_id=cls_a.id, subject_id=english.id,
            is_compulsory=True, display_order=1,
        ),
    ])
    await db.commit()

    # Session-level tenant / user context so services pick them up.
    tenant_tok = _tenant_id.set(tid)
    user_tok = _current_user_id.set(admin.id)
    role_tok = _current_user_role.set(admin.role)

    try:
        yield {
            "tenant": tenant, "tid": tid, "admin": admin,
            "cls_a": cls_a, "cls_b": cls_b,
            "maths": maths, "english": english,
        }
    finally:
        _current_user_role.reset(role_tok)
        _current_user_id.reset(user_tok)
        _tenant_id.reset(tenant_tok)
        from sqlalchemy import text as _text
        async with db.bind.connect() as conn:
            await conn.execute(
                _text("DELETE FROM tenants WHERE id = :id"), {"id": tid}
            )
            await conn.commit()


async def _make_teacher(db: AsyncSession, tenant_id: uuid.UUID) -> User:
    t = User(
        id=uuid.uuid4(), tenant_id=tenant_id,
        email=f"t-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("x"),
        first_name="Taught", last_name="Teacher",
        role=Role.TEACHER.value, is_active=True,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


# ----------------------------------------------------------------------
# Invitation flow
# ----------------------------------------------------------------------


class TestInviteWithAssignments:
    @pytest.mark.asyncio
    async def test_invite_stores_mixed_plan(self, db, world):
        """One general + one subject-only item persists correctly."""
        svc = get_teacher_invitation_service()
        inv = await svc.create_invitation(
            db,
            email="hire-mixed@example.com",
            first_name="Hire", last_name="Mixed",
            intended_assignments=[
                {
                    "class_id": str(world["cls_a"].id),
                    "is_primary": True,
                    "subject_ids": [],
                },
                {
                    "class_id": str(world["cls_a"].id),
                    "is_primary": False,
                    "subject_ids": [str(world["english"].id)],
                },
            ],
        )
        assert len(inv.intended_assignments) == 2
        assert inv.intended_assignments[0]["is_primary"] is True
        assert inv.intended_assignments[1]["subject_ids"] == [
            str(world["english"].id)
        ]

    @pytest.mark.asyncio
    async def test_invite_rejects_subjects_when_mode_off(self, db, world):
        """Flipping subject_teacher_enabled off blocks subject plans."""
        world["tenant"].settings = {
            **world["tenant"].settings, "subject_teacher_enabled": False,
        }
        await db.commit()

        svc = get_teacher_invitation_service()
        with pytest.raises(ValueError, match="Subject-specialist mode"):
            await svc.create_invitation(
                db,
                email="hire-blocked@example.com",
                first_name="No", last_name="Go",
                intended_assignments=[
                    {
                        "class_id": str(world["cls_a"].id),
                        "subject_ids": [str(world["maths"].id)],
                    },
                ],
            )

    @pytest.mark.asyncio
    async def test_invite_rejects_cross_tenant_class(self, db, world):
        """A class_id from another tenant must not sneak in."""
        other_tid = uuid.uuid4()
        other_tenant = Tenant(
            id=other_tid, name=f"Other {other_tid.hex[:6]}",
            slug=f"other-{other_tid.hex[:8]}",
            email=f"a@{other_tid.hex[:6]}.example.com",
            education_type="HIGH_SCHOOL",
            settings={}, is_active=True, onboarding_completed=True,
        )
        other_cls = SchoolClass(
            id=uuid.uuid4(), tenant_id=other_tid,
            name="Alien 1A", is_active=True,
        )
        db.add_all([other_tenant, other_cls])
        await db.commit()

        svc = get_teacher_invitation_service()
        try:
            with pytest.raises(ValueError, match="Class not found"):
                await svc.create_invitation(
                    db,
                    email="hire-xtenant@example.com",
                    first_name="X", last_name="Tenant",
                    intended_assignments=[
                        {"class_id": str(other_cls.id)},
                    ],
                )
        finally:
            from sqlalchemy import text as _text
            async with db.bind.connect() as conn:
                await conn.execute(
                    _text("DELETE FROM tenants WHERE id = :id"),
                    {"id": other_tid},
                )
                await conn.commit()

    @pytest.mark.asyncio
    async def test_accept_applies_rows(self, db, world):
        """Accepting the invite creates both TeacherClass +
        ClassSubjectTeacher rows for the right (class, subject)."""
        svc = get_teacher_invitation_service()
        inv = await svc.create_invitation(
            db,
            email="hire-accept@example.com",
            first_name="Jo", last_name="Doe",
            intended_assignments=[
                {
                    "class_id": str(world["cls_a"].id),
                    "is_primary": True,
                    "subject_ids": [str(world["maths"].id)],
                },
            ],
        )

        teacher = await _make_teacher(db, world["tid"])

        ok = await svc.accept_invitation(
            db, code=inv.invitation_code, teacher_id=teacher.id,
        )
        assert ok is True

        general = (await db.execute(
            select(TeacherClass).where(
                TeacherClass.teacher_id == teacher.id,
            )
        )).scalars().all()
        subject = (await db.execute(
            select(ClassSubjectTeacher).where(
                ClassSubjectTeacher.teacher_id == teacher.id,
            )
        )).scalars().all()
        assert len(general) == 1
        assert general[0].class_id == world["cls_a"].id
        assert general[0].is_primary is True
        assert len(subject) == 1
        assert subject[0].subject_id == world["maths"].id

    @pytest.mark.asyncio
    async def test_apply_is_idempotent(self, db, world):
        """Replaying the apply step doesn't create duplicate rows."""
        svc = get_teacher_invitation_service()
        inv = await svc.create_invitation(
            db,
            email="hire-replay@example.com",
            first_name="Re", last_name="Play",
            intended_assignments=[
                {
                    "class_id": str(world["cls_a"].id),
                    "subject_ids": [str(world["maths"].id)],
                },
            ],
        )
        teacher = await _make_teacher(db, world["tid"])

        # First apply inside accept_invitation.
        await svc.accept_invitation(
            db, code=inv.invitation_code, teacher_id=teacher.id,
        )
        # Second apply directly against the already-ACCEPTED invitation.
        summary = await svc._apply_intended_assignments(
            db, inv, teacher.id,
        )
        await db.commit()

        subject = (await db.execute(
            select(ClassSubjectTeacher).where(
                ClassSubjectTeacher.teacher_id == teacher.id,
            )
        )).scalars().all()
        assert len(subject) == 1
        assert summary["created_subject"] == 0
        assert summary["created_general"] == 0


# ----------------------------------------------------------------------
# Direct per-teacher assignment management
# ----------------------------------------------------------------------


class TestAssignmentManagement:
    @pytest.mark.asyncio
    async def test_add_general_bumps_existing_class_teacher(self, db, world):
        """One class teacher per class. Assigning teacher B to a class
        that already has teacher A deletes A's row and leaves exactly
        one TeacherClass row (B, is_primary=True)."""
        svc = get_class_service()
        a = await _make_teacher(db, world["tid"])
        b = await _make_teacher(db, world["tid"])

        await svc.add_general_assignment(
            db, teacher_id=a.id, class_id=world["cls_a"].id,
        )
        await db.commit()

        await svc.add_general_assignment(
            db, teacher_id=b.id, class_id=world["cls_a"].id,
        )
        await db.commit()

        rows = (await db.execute(
            select(TeacherClass).where(
                TeacherClass.class_id == world["cls_a"].id,
            )
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].teacher_id == b.id
        assert rows[0].is_primary is True

    @pytest.mark.asyncio
    async def test_remove_subject_missing_is_false(self, db, world):
        """Removing a non-existent subject row returns False, no raise."""
        svc = get_class_service()
        teacher = await _make_teacher(db, world["tid"])
        removed = await svc.remove_subject_assignment(
            db,
            teacher_id=teacher.id,
            class_id=world["cls_a"].id,
            subject_id=world["english"].id,
        )
        assert removed is False

    @pytest.mark.asyncio
    async def test_cross_tenant_teacher_raises_not_found(self, db, world):
        """Trying to assign a class on this tenant to a teacher that
        lives on ANOTHER tenant must raise NotFoundException, not
        silently create a dangling row."""
        other_tid = uuid.uuid4()
        other_tenant = Tenant(
            id=other_tid, name=f"OT {other_tid.hex[:6]}",
            slug=f"ot-{other_tid.hex[:8]}",
            email=f"a@{other_tid.hex[:6]}.example.com",
            education_type="HIGH_SCHOOL",
            settings={}, is_active=True, onboarding_completed=True,
        )
        other_teacher = User(
            id=uuid.uuid4(), tenant_id=other_tid,
            email=f"ot-{other_tid.hex[:6]}@example.com",
            password_hash=hash_password("x"),
            first_name="Other", last_name="Teacher",
            role=Role.TEACHER.value, is_active=True,
        )
        db.add_all([other_tenant, other_teacher])
        await db.commit()
        try:
            svc = get_class_service()
            with pytest.raises(NotFoundException):
                await svc.add_general_assignment(
                    db,
                    teacher_id=other_teacher.id,
                    class_id=world["cls_a"].id,
                )
        finally:
            from sqlalchemy import text as _text
            async with db.bind.connect() as conn:
                await conn.execute(
                    _text("DELETE FROM tenants WHERE id = :id"),
                    {"id": other_tid},
                )
                await conn.commit()
