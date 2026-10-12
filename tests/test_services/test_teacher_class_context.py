"""Regression test for the dashboard 500 when a teacher is primary
of multiple classes.

Before the fix, get_teacher_class_context() used scalar_one_or_none()
to pick the primary assignment, which blew up with
sqlalchemy.exc.MultipleResultsFound when a teacher legitimately had
is_primary=True on two or more classes.

The fix uses .first() with an explicit ordering so it tolerates the
case without crashing.
"""

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolClass, TeacherClass, Tenant, User
from app.models.user import Role
from app.utils.security import hash_password
from app.utils.tenant_context import _current_user_id, _current_user_role, _tenant_id
from app.web.helpers import get_teacher_class_context


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — only .cookies is used."""

    def __init__(self, cookies: dict | None = None):
        self.cookies = cookies or {}


async def _make_teacher_with_classes(
    db: AsyncSession, tenant: Tenant, class_count: int, primary_count: int
) -> User:
    """Create a teacher assigned to N classes, M of which are marked primary."""
    teacher = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=f"t-{uuid.uuid4().hex[:6]}@test.local",
        password_hash=hash_password("x"),
        first_name="Multi",
        last_name="Primary",
        role=Role.TEACHER.value,
        is_active=True,
        language="en",
    )
    db.add(teacher)
    await db.commit()

    for i in range(class_count):
        cls = SchoolClass(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name=f"Class {i + 1}",
            is_active=True,
        )
        db.add(cls)
        await db.flush()
        assignment = TeacherClass(
            id=uuid.uuid4(),
            teacher_id=teacher.id,
            class_id=cls.id,
            is_primary=(i < primary_count),
        )
        db.add(assignment)
    await db.commit()
    return teacher


class TestTeacherClassContext:
    async def test_single_primary_picks_that_class(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        teacher = await _make_teacher_with_classes(db, test_tenant, class_count=3, primary_count=1)

        # Set request-context vars the helper depends on
        uid_tok = _current_user_id.set(teacher.id)
        tid_tok = _tenant_id.set(test_tenant.id)
        role_tok = _current_user_role.set(Role.TEACHER.value)
        try:
            ctx = await get_teacher_class_context(_FakeRequest(), db)
        finally:
            _current_user_role.reset(role_tok)
            _current_user_id.reset(uid_tok)
            _tenant_id.reset(tid_tok)

        assert ctx["selected_class"] is not None
        assert len(ctx["teacher_classes"]) == 3

    async def test_multiple_primaries_do_not_crash(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """Regression: teacher with 2+ primaries used to raise MultipleResultsFound."""
        teacher = await _make_teacher_with_classes(db, test_tenant, class_count=3, primary_count=2)

        uid_tok = _current_user_id.set(teacher.id)
        tid_tok = _tenant_id.set(test_tenant.id)
        role_tok = _current_user_role.set(Role.TEACHER.value)
        try:
            # Previously this raised sqlalchemy.exc.MultipleResultsFound
            ctx = await get_teacher_class_context(_FakeRequest(), db)
        finally:
            _current_user_role.reset(role_tok)
            _current_user_id.reset(uid_tok)
            _tenant_id.reset(tid_tok)

        # Must succeed and return a valid selection
        assert ctx["selected_class"] is not None
        assert len(ctx["teacher_classes"]) == 3

    async def test_no_primary_falls_back_to_first_class(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        teacher = await _make_teacher_with_classes(db, test_tenant, class_count=2, primary_count=0)

        uid_tok = _current_user_id.set(teacher.id)
        tid_tok = _tenant_id.set(test_tenant.id)
        role_tok = _current_user_role.set(Role.TEACHER.value)
        try:
            ctx = await get_teacher_class_context(_FakeRequest(), db)
        finally:
            _current_user_role.reset(role_tok)
            _current_user_id.reset(uid_tok)
            _tenant_id.reset(tid_tok)

        assert ctx["selected_class"] is not None
        assert ctx["selected_class"].id in [c.id for c in ctx["teacher_classes"]]

    async def test_cookie_selection_overrides_primary(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        teacher = await _make_teacher_with_classes(db, test_tenant, class_count=3, primary_count=1)

        uid_tok = _current_user_id.set(teacher.id)
        tid_tok = _tenant_id.set(test_tenant.id)
        role_tok = _current_user_role.set(Role.TEACHER.value)
        try:
            # First call with no cookie picks the primary
            default_ctx = await get_teacher_class_context(_FakeRequest(), db)
            primary_id = default_ctx["selected_class"].id

            # Pick a different class via cookie
            other = next(c for c in default_ctx["teacher_classes"] if c.id != primary_id)
            req_with_cookie = _FakeRequest({"selected_class_id": str(other.id)})
            ctx = await get_teacher_class_context(req_with_cookie, db)
            assert ctx["selected_class"].id == other.id
        finally:
            _current_user_role.reset(role_tok)
            _current_user_id.reset(uid_tok)
            _tenant_id.reset(tid_tok)

    async def test_general_teacher_is_not_subject_only(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """A general class teacher should not be flagged subject-only."""
        teacher = await _make_teacher_with_classes(db, test_tenant, class_count=2, primary_count=1)

        uid_tok = _current_user_id.set(teacher.id)
        tid_tok = _tenant_id.set(test_tenant.id)
        role_tok = _current_user_role.set(Role.TEACHER.value)
        try:
            ctx = await get_teacher_class_context(_FakeRequest(), db)
        finally:
            _current_user_role.reset(role_tok)
            _current_user_id.reset(uid_tok)
            _tenant_id.reset(tid_tok)

        assert ctx["is_subject_only_for_selected"] is False
        assert ctx["selected_class"].id in ctx["general_class_ids"]

    async def test_subject_only_teacher_is_flagged(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """A teacher that only owns a subject in a class (no TeacherClass
        row) should be flagged subject-only. UI uses this to hide the
        Take Attendance CTAs that would hit the server-side gate."""
        from app.models.academic import ClassSubject, ClassSubjectTeacher, Subject

        teacher = User(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            email=f"so-{uuid.uuid4().hex[:6]}@test.local",
            password_hash=hash_password("x"),
            first_name="Subject",
            last_name="Only",
            role=Role.TEACHER.value,
            is_active=True,
            language="en",
        )
        db.add(teacher)

        # Enable subject-teacher mode on the tenant — otherwise
        # get_teacher_classes drops ClassSubjectTeacher rows.
        settings = dict(test_tenant.settings or {})
        settings["subject_teacher_enabled"] = True
        test_tenant.settings = settings

        klass = SchoolClass(
            id=uuid.uuid4(), tenant_id=test_tenant.id, name="SO Class", is_active=True,
        )
        subject = Subject(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            name="Mathematics", code=f"MATH-{uuid.uuid4().hex[:6]}",
            default_total_marks=100,
        )
        db.add_all([klass, subject])
        await db.flush()
        db.add_all([
            ClassSubject(id=uuid.uuid4(), class_id=klass.id, subject_id=subject.id),
            ClassSubjectTeacher(
                id=uuid.uuid4(),
                class_id=klass.id, subject_id=subject.id, teacher_id=teacher.id,
            ),
        ])
        await db.commit()

        uid_tok = _current_user_id.set(teacher.id)
        tid_tok = _tenant_id.set(test_tenant.id)
        role_tok = _current_user_role.set(Role.TEACHER.value)
        try:
            ctx = await get_teacher_class_context(_FakeRequest(), db)
        finally:
            _current_user_role.reset(role_tok)
            _current_user_id.reset(uid_tok)
            _tenant_id.reset(tid_tok)

        assert ctx["selected_class"] is not None
        assert ctx["selected_class"].id == klass.id
        assert ctx["is_subject_only_for_selected"] is True
        assert ctx["general_class_ids"] == set()
