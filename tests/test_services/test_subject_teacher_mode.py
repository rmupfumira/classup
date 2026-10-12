"""Subject-specialist teacher mode (task #157, 2026-10-12).

Three scenarios drive the design and so drive the tests:

A. **Feature OFF** (default) — the general class teacher behaves
   exactly as before. Subject-teacher table is irrelevant.
B. **Feature ON, subject-specialist teacher** — can grade / comment
   / message ONLY on their assigned subject(s). Attendance stays
   with the general class teacher regardless.
C. **Feature ON, general class teacher** — still sees everything,
   even if they're ALSO assigned to specific subjects elsewhere.

The permission gate lives in
``academic_service.can_teacher_grade_subject`` and the message gate
in ``message_service._validate_can_message``. Both are exercised
here against the real database so the SQL + indexes stay honest.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ForbiddenException
from app.models import (
    ClassSubject,
    ClassSubjectTeacher,
    ParentStudent,
    SchoolClass,
    Student,
    Subject,
    TeacherClass,
    Tenant,
    User,
)
from app.models.user import Role
from app.services.academic_service import get_academic_service
from app.services.message_service import get_message_service
from app.utils.security import hash_password


@pytest_asyncio.fixture
async def world(db: AsyncSession):
    """One tenant, one class, one subject assigned to the class, two
    teachers (a general class teacher + a subject specialist), one
    student with a linked parent.

    Fixture builds everything with ``subject_teacher_enabled=True``
    so the ON path is the default — tests that want OFF behaviour
    flip the tenant setting mid-test.
    """
    tid = uuid.uuid4()
    tenant = Tenant(
        id=tid, name=f"ST {tid.hex[:6]}", slug=f"st-{tid.hex[:8]}",
        email=f"admin@{tid.hex[:6]}.example.com",
        education_type="HIGH_SCHOOL",
        settings={"subject_teacher_enabled": True},
        is_active=True, onboarding_completed=True,
    )
    class_teacher = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"ct-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Class", last_name="Teacher",
        role=Role.TEACHER.value, is_active=True,
    )
    subject_teacher = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"st-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Subject", last_name="Specialist",
        role=Role.TEACHER.value, is_active=True,
    )
    parent = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"parent-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Mum", last_name="Parent",
        role=Role.PARENT.value, is_active=True,
    )
    cls = SchoolClass(
        id=uuid.uuid4(), tenant_id=tid, name="Form 4A", is_active=True,
    )
    other_cls = SchoolClass(
        id=uuid.uuid4(), tenant_id=tid, name="Form 4B", is_active=True,
    )
    subj = Subject(
        id=uuid.uuid4(), tenant_id=tid,
        name="Mathematics", code="MATH", default_total_marks=100,
        is_active=True,
    )
    other_subj = Subject(
        id=uuid.uuid4(), tenant_id=tid,
        name="History", code="HIST", default_total_marks=100,
        is_active=True,
    )
    student = Student(
        id=uuid.uuid4(), tenant_id=tid,
        first_name="Kid", last_name="One",
        class_id=cls.id, is_active=True,
    )
    db.add_all([
        tenant, class_teacher, subject_teacher, parent,
        cls, other_cls, subj, other_subj, student,
    ])
    await db.flush()

    # Link subject to the class (prerequisite for subject-teacher).
    db.add(ClassSubject(
        id=uuid.uuid4(), class_id=cls.id, subject_id=subj.id,
        is_compulsory=True, display_order=0,
    ))
    # The general class teacher.
    db.add(TeacherClass(
        id=uuid.uuid4(), teacher_id=class_teacher.id, class_id=cls.id,
        is_primary=True,
    ))
    # Link parent to student.
    db.add(ParentStudent(
        id=uuid.uuid4(), parent_id=parent.id, student_id=student.id,
        relationship_type="PARENT", is_primary=True,
    ))
    await db.commit()

    yield {
        "tenant": tenant, "tid": tid,
        "class_teacher": class_teacher,
        "subject_teacher": subject_teacher,
        "parent": parent,
        "cls": cls, "other_cls": other_cls,
        "subj": subj, "other_subj": other_subj,
        "student": student,
    }

    from sqlalchemy import text as _text
    async with db.bind.connect() as conn:
        await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": tid})
        await conn.commit()


class TestAssignmentCrud:
    @pytest.mark.asyncio
    async def test_assign_is_idempotent(self, db, world):
        svc = get_academic_service()
        from app.utils.tenant_context import _tenant_id

        tok = _tenant_id.set(world["tid"])
        try:
            a = await svc.assign_subject_teacher(
                db, class_id=world["cls"].id, subject_id=world["subj"].id,
                teacher_id=world["subject_teacher"].id,
            )
            b = await svc.assign_subject_teacher(
                db, class_id=world["cls"].id, subject_id=world["subj"].id,
                teacher_id=world["subject_teacher"].id,
            )
        finally:
            _tenant_id.reset(tok)
        assert a.id == b.id  # Same row, not a duplicate.

    @pytest.mark.asyncio
    async def test_cannot_assign_when_subject_not_on_class(self, db, world):
        """Guards against assigning a teacher to a subject that
        isn't even taught in the class — would be a dangling row
        the UI could never surface."""
        svc = get_academic_service()
        from app.exceptions import ValidationException
        from app.utils.tenant_context import _tenant_id

        tok = _tenant_id.set(world["tid"])
        try:
            with pytest.raises(ValidationException):
                await svc.assign_subject_teacher(
                    db, class_id=world["cls"].id,
                    subject_id=world["other_subj"].id,  # not on this class
                    teacher_id=world["subject_teacher"].id,
                )
        finally:
            _tenant_id.reset(tok)


class TestGradingPermissionGate:
    @pytest.mark.asyncio
    async def test_subject_teacher_can_grade_their_subject(self, db, world):
        svc = get_academic_service()
        from app.utils.tenant_context import _tenant_id

        tok = _tenant_id.set(world["tid"])
        try:
            await svc.assign_subject_teacher(
                db, class_id=world["cls"].id, subject_id=world["subj"].id,
                teacher_id=world["subject_teacher"].id,
            )
        finally:
            _tenant_id.reset(tok)

        allowed = await svc.can_teacher_grade_subject(
            db,
            teacher_id=world["subject_teacher"].id,
            class_id=world["cls"].id,
            subject_id=world["subj"].id,
            subject_teacher_mode=True,
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_subject_teacher_cannot_grade_other_subjects(self, db, world):
        """Specialist is pinned to one subject — grading History
        (which they aren't assigned to) is blocked even if History
        belonged to the class, which it doesn't here."""
        svc = get_academic_service()
        from app.utils.tenant_context import _tenant_id

        tok = _tenant_id.set(world["tid"])
        try:
            await svc.assign_subject_teacher(
                db, class_id=world["cls"].id, subject_id=world["subj"].id,
                teacher_id=world["subject_teacher"].id,
            )
        finally:
            _tenant_id.reset(tok)

        allowed = await svc.can_teacher_grade_subject(
            db,
            teacher_id=world["subject_teacher"].id,
            class_id=world["cls"].id,
            subject_id=world["other_subj"].id,
            subject_teacher_mode=True,
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_general_class_teacher_can_grade_everything_in_their_class(
        self, db, world,
    ):
        """The audit's #4 answer: 'see only what they teach UNLESS
        it's their class as well'. If they're the class teacher,
        full access to every subject."""
        svc = get_academic_service()
        allowed = await svc.can_teacher_grade_subject(
            db,
            teacher_id=world["class_teacher"].id,
            class_id=world["cls"].id,
            subject_id=world["subj"].id,
            subject_teacher_mode=True,
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_mode_off_subject_teacher_table_ignored(self, db, world):
        """Feature OFF: the specialist assignment exists in the DB
        but the gate pretends it's not there. Only teacher_classes
        matters — matches pre-feature behaviour."""
        svc = get_academic_service()
        from app.utils.tenant_context import _tenant_id

        tok = _tenant_id.set(world["tid"])
        try:
            await svc.assign_subject_teacher(
                db, class_id=world["cls"].id, subject_id=world["subj"].id,
                teacher_id=world["subject_teacher"].id,
            )
        finally:
            _tenant_id.reset(tok)

        allowed = await svc.can_teacher_grade_subject(
            db,
            teacher_id=world["subject_teacher"].id,
            class_id=world["cls"].id,
            subject_id=world["subj"].id,
            subject_teacher_mode=False,
        )
        assert allowed is False


class TestMessagePermissionGate:
    """The message guard extends subject-teacher access only when the
    tenant setting is on. A specialist on an OFF-tenant still can't
    message parents in a class they don't teach generally."""

    @pytest.mark.asyncio
    async def test_subject_teacher_can_message_parent_when_mode_on(
        self, db, world,
    ):
        svc = get_academic_service()
        from app.utils.tenant_context import _tenant_id

        tok = _tenant_id.set(world["tid"])
        try:
            await svc.assign_subject_teacher(
                db, class_id=world["cls"].id, subject_id=world["subj"].id,
                teacher_id=world["subject_teacher"].id,
            )
        finally:
            _tenant_id.reset(tok)

        # Tenant has subject_teacher_enabled=True from the fixture.
        tok = _tenant_id.set(world["tid"])
        try:
            await get_message_service()._validate_can_message(
                db,
                sender_id=world["subject_teacher"].id,
                sender_role="TEACHER",
                student_id=world["student"].id,
                recipient_id=world["parent"].id,
            )
        finally:
            _tenant_id.reset(tok)
        # Reaching here = no exception = allowed.

    @pytest.mark.asyncio
    async def test_subject_teacher_blocked_when_mode_off(self, db, world):
        """Same specialist assignment, but feature flag is off →
        back to the strict pre-feature behaviour."""
        svc = get_academic_service()
        from app.utils.tenant_context import _tenant_id

        tok = _tenant_id.set(world["tid"])
        try:
            await svc.assign_subject_teacher(
                db, class_id=world["cls"].id, subject_id=world["subj"].id,
                teacher_id=world["subject_teacher"].id,
            )
        finally:
            _tenant_id.reset(tok)

        # Flip the flag OFF on the tenant.
        world["tenant"].settings = {
            **world["tenant"].settings, "subject_teacher_enabled": False,
        }
        await db.commit()

        tok = _tenant_id.set(world["tid"])
        try:
            with pytest.raises(ForbiddenException):
                await get_message_service()._validate_can_message(
                    db,
                    sender_id=world["subject_teacher"].id,
                    sender_role="TEACHER",
                    student_id=world["student"].id,
                    recipient_id=world["parent"].id,
                )
        finally:
            _tenant_id.reset(tok)

    @pytest.mark.asyncio
    async def test_general_class_teacher_unaffected_by_mode(self, db, world):
        """Flipping the mode doesn't change what the class teacher
        can do — they always qualify via the teacher_classes row."""
        from app.utils.tenant_context import _tenant_id

        tok = _tenant_id.set(world["tid"])
        try:
            # Mode ON (fixture default) — fine.
            await get_message_service()._validate_can_message(
                db,
                sender_id=world["class_teacher"].id,
                sender_role="TEACHER",
                student_id=world["student"].id,
                recipient_id=world["parent"].id,
            )
            # Flip to OFF — still fine.
            world["tenant"].settings = {
                **world["tenant"].settings,
                "subject_teacher_enabled": False,
            }
            await db.commit()
            await get_message_service()._validate_can_message(
                db,
                sender_id=world["class_teacher"].id,
                sender_role="TEACHER",
                student_id=world["student"].id,
                recipient_id=world["parent"].id,
            )
        finally:
            _tenant_id.reset(tok)


class TestTenantSetting:
    def test_default_is_off(self):
        """Pre-feature tenants must not light up the mode by accident."""
        from app.models.tenant import Tenant as T
        t = T(settings={})
        assert t.subject_teacher_enabled is False

    def test_setting_true_is_reflected(self):
        from app.models.tenant import Tenant as T
        t = T(settings={"subject_teacher_enabled": True})
        assert t.subject_teacher_enabled is True
