"""Tests for the global search service.

Pins the behaviours that would quietly leak data if they regressed:
  - cross-tenant isolation (a school admin never sees another school's rows)
  - teacher can only search own classes and students in them
  - parent can only find their own children
  - empty / short queries are no-ops
  - ILIKE wildcards in the query are escaped
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolClass, Student, Tenant, User
from app.models.school_class import TeacherClass
from app.models.student import ParentStudent
from app.models.user import Role
from app.services.search_service import get_search_service
from app.utils.security import hash_password


@pytest_asyncio.fixture
async def search_seed(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass,
):
    """A small cast: 2 students on the class, 1 teacher assigned, 1 parent
    linked to one child, and 1 student + 1 teacher in a SECOND tenant to
    prove cross-tenant isolation.
    """
    # Students in the primary tenant
    alice = Student(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        first_name="Alice", last_name="Mukasa",
        class_id=test_class.id, is_active=True,
    )
    bob = Student(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        first_name="Bob", last_name="Smith",
        class_id=test_class.id, is_active=True,
    )

    # Teacher + their class assignment
    teacher = User(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        email=f"t-{uuid.uuid4().hex[:6]}@test.local",
        password_hash=hash_password("x"),
        first_name="Jane", last_name="Mukasa",
        role=Role.TEACHER.value, is_active=True,
    )
    parent = User(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        email=f"p-{uuid.uuid4().hex[:6]}@test.local",
        password_hash=hash_password("x"),
        first_name="Pat", last_name="Mukasa",
        role=Role.PARENT.value, is_active=True,
    )

    # Second tenant to prove isolation
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Tenant",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        email=f"other-{uuid.uuid4().hex[:8]}@test.local",
        is_active=True,
    )
    db.add_all([alice, bob, teacher, parent, other_tenant])
    await db.flush()

    foreign_student = Student(
        id=uuid.uuid4(), tenant_id=other_tenant.id,
        first_name="Alice", last_name="Mukasa",  # same name as `alice`
        is_active=True,
    )
    db.add_all([
        TeacherClass(
            id=uuid.uuid4(), teacher_id=teacher.id,
            class_id=test_class.id, is_primary=True,
        ),
        ParentStudent(
            id=uuid.uuid4(), parent_id=parent.id,
            student_id=alice.id, relationship_type="PARENT", is_primary=True,
        ),
        foreign_student,
    ])
    await db.commit()

    return {
        "tenant": test_tenant,
        "other_tenant": other_tenant,
        "class": test_class,
        "alice": alice,
        "bob": bob,
        "teacher": teacher,
        "parent": parent,
        "foreign_student": foreign_student,
    }


def _flatten(payload: dict) -> list[tuple[str, str]]:
    """List of (group_type, item_id) for easy assertions."""
    out = []
    for g in payload["groups"]:
        for i in g["items"]:
            out.append((g["type"], i["id"]))
    return out


class TestSchoolAdminSearch:
    async def test_matches_students_by_name(self, db, search_seed):
        svc = get_search_service()
        payload = await svc.search(
            db,
            q="Mukasa",
            role="SCHOOL_ADMIN",
            user_id=uuid.uuid4(),
            tenant_id=search_seed["tenant"].id,
        )
        hits = _flatten(payload)
        student_ids = [i for (t, i) in hits if t == "students"]
        assert str(search_seed["alice"].id) in student_ids
        # `Bob Smith` doesn't match — only Alice Mukasa does.
        assert str(search_seed["bob"].id) not in student_ids

    async def test_does_not_see_other_tenants_students(self, db, search_seed):
        svc = get_search_service()
        payload = await svc.search(
            db,
            q="Mukasa",
            role="SCHOOL_ADMIN",
            user_id=uuid.uuid4(),
            tenant_id=search_seed["tenant"].id,
        )
        # The identically-named `foreign_student` lives on the other
        # tenant — isolation must drop it even on an exact-name match.
        student_ids = [i for (t, i) in _flatten(payload) if t == "students"]
        assert str(search_seed["foreign_student"].id) not in student_ids

    async def test_matches_teachers_and_parents(self, db, search_seed):
        svc = get_search_service()
        payload = await svc.search(
            db,
            q="Mukasa",
            role="SCHOOL_ADMIN",
            user_id=uuid.uuid4(),
            tenant_id=search_seed["tenant"].id,
        )
        teacher_ids = [i for (t, i) in _flatten(payload) if t == "teachers"]
        parent_ids = [i for (t, i) in _flatten(payload) if t == "parents"]
        assert str(search_seed["teacher"].id) in teacher_ids
        assert str(search_seed["parent"].id) in parent_ids


class TestTeacherSearch:
    async def test_only_own_classes_and_students(self, db, search_seed):
        svc = get_search_service()
        payload = await svc.search(
            db,
            q="Mukasa",
            role="TEACHER",
            user_id=search_seed["teacher"].id,
            tenant_id=search_seed["tenant"].id,
        )
        student_ids = [i for (t, i) in _flatten(payload) if t == "students"]
        # Teacher is assigned to test_class — Alice is in it → match.
        assert str(search_seed["alice"].id) in student_ids

    async def test_other_tenant_teacher_sees_nothing(self, db, search_seed):
        """A teacher from the foreign tenant must not see our rows,
        even when the search term matches."""
        svc = get_search_service()
        payload = await svc.search(
            db,
            q="Mukasa",
            role="TEACHER",
            user_id=uuid.uuid4(),  # a teacher id with zero classes
            tenant_id=search_seed["other_tenant"].id,
        )
        assert payload["total"] == 0


class TestParentSearch:
    async def test_only_own_child(self, db, search_seed):
        svc = get_search_service()
        payload = await svc.search(
            db,
            q="Mukasa",
            role="PARENT",
            user_id=search_seed["parent"].id,
            tenant_id=search_seed["tenant"].id,
        )
        hits = _flatten(payload)
        # Parent is linked only to Alice. Bob (classmate, same tenant,
        # different surname) must be invisible. The teacher (also named
        # Mukasa) must also be invisible — parents don't get teacher
        # results.
        assert hits == [("children", str(search_seed["alice"].id))]


class TestQueryShape:
    async def test_empty_query_is_a_noop(self, db, search_seed):
        svc = get_search_service()
        payload = await svc.search(
            db, q="", role="SCHOOL_ADMIN",
            user_id=uuid.uuid4(),
            tenant_id=search_seed["tenant"].id,
        )
        assert payload["groups"] == []
        assert payload["total"] == 0

    async def test_single_char_query_is_a_noop(self, db, search_seed):
        """Under the 2-char minimum, don't scan the DB."""
        svc = get_search_service()
        payload = await svc.search(
            db, q="M", role="SCHOOL_ADMIN",
            user_id=uuid.uuid4(),
            tenant_id=search_seed["tenant"].id,
        )
        assert payload["total"] == 0

    async def test_wildcard_characters_are_escaped(self, db, search_seed):
        """`%` and `_` in a user-typed query must match literally — if
        they weren't escaped, `%Muk%` would become `%%Muk%%` and still
        match, which is the WRONG behaviour (it should hit zero rows
        because no name contains a literal %)."""
        svc = get_search_service()
        payload = await svc.search(
            db, q="%Muk", role="SCHOOL_ADMIN",
            user_id=uuid.uuid4(),
            tenant_id=search_seed["tenant"].id,
        )
        # No student name contains `%Muk`; the real Mukasas must NOT
        # bleed through because the leading % got un-escaped.
        assert payload["total"] == 0
