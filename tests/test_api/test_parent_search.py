"""Tests for the parent search endpoint that powers the invite-parent
typeahead + the sibling picker.

Two things matter:

  1. **Tenant isolation** — a school admin at Tenant A must never see a
     parent from Tenant B in their search results, even if the email
     matches. This is the same guarantee every other tenant-scoped
     endpoint holds; the search endpoint is a new attack surface so
     it gets its own test.

  2. **Children context** — the whole point of the endpoint (over
     just listing users) is that admins can confirm a parent match by
     seeing which children they're already linked to. If that context
     goes missing the UX collapses back to "type an email and hope".
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import users as users_api
from app.models import ParentStudent, SchoolClass, Student, Tenant, User
from app.models.user import Role
from app.utils.security import hash_password
from app.utils.tenant_context import _current_user_id, _current_user_role, _tenant_id


@pytest_asyncio.fixture
async def two_tenants_with_parents(db: AsyncSession):
    """Build two tenants, each with a parent + child. Parent B has the
    same email as parent A (unlikely in practice, but the ONLY thing
    stopping cross-tenant leak here is our filter — test it)."""
    tid_a, tid_b = uuid.uuid4(), uuid.uuid4()

    def _mk_tenant(tid, name):
        slug = f"{name}-{tid.hex[:6]}"
        return Tenant(
            id=tid, name=name, slug=slug,
            email=f"admin@{slug}.test",
            education_type="PRIMARY_SCHOOL",
            settings={"features": {}},
            is_active=True, onboarding_completed=True,
        )
    tenant_a = _mk_tenant(tid_a, "ta")
    tenant_b = _mk_tenant(tid_b, "tb")
    db.add_all([tenant_a, tenant_b])
    await db.flush()

    def _mk_parent(tid, first, email):
        return User(
            id=uuid.uuid4(), tenant_id=tid,
            email=email, password_hash=hash_password("x"),
            first_name=first, last_name="Doe",
            role=Role.PARENT.value, is_active=True,
        )
    parent_a = _mk_parent(tid_a, "Alice", "duplicate@parent.test")
    parent_b = _mk_parent(tid_b, "Bob",   "duplicate@parent.test")  # same email
    parent_a_2 = _mk_parent(tid_a, "Anna", "anna@parent.test")
    db.add_all([parent_a, parent_b, parent_a_2])
    await db.flush()

    # Give Alice a child (proves children_context appears in results).
    school_class_a = SchoolClass(
        id=uuid.uuid4(), tenant_id=tid_a, name="Grade 1A", is_active=True,
    )
    db.add(school_class_a)
    await db.flush()
    child_a = Student(
        id=uuid.uuid4(), tenant_id=tid_a,
        first_name="Sarah", last_name="Doe",
        class_id=school_class_a.id, is_active=True,
    )
    db.add(child_a)
    await db.flush()
    db.add(ParentStudent(
        id=uuid.uuid4(), parent_id=parent_a.id, student_id=child_a.id,
        relationship_type="PARENT", is_primary=True,
    ))
    await db.commit()

    try:
        yield SimpleNamespace(
            tenant_a=tenant_a, tenant_b=tenant_b,
            parent_a=parent_a, parent_b=parent_b, parent_a_2=parent_a_2,
            child_a=child_a,
            tid_a=tid_a, tid_b=tid_b,
        )
    finally:
        from app.database import get_db_context
        from sqlalchemy import text as sql_text
        async with get_db_context() as db2:
            await db2.execute(
                sql_text("DELETE FROM tenants WHERE id IN (:a, :b)"),
                {"a": tid_a, "b": tid_b},
            )
            await db2.commit()


@pytest.fixture
def _as_admin_of_tenant_a(two_tenants_with_parents):
    """Impersonate a school admin of tenant A for the search call."""
    tok_t = _tenant_id.set(two_tenants_with_parents.tid_a)
    tok_r = _current_user_role.set("SCHOOL_ADMIN")
    tok_u = _current_user_id.set(uuid.uuid4())
    try:
        yield
    finally:
        _current_user_id.reset(tok_u)
        _current_user_role.reset(tok_r)
        _tenant_id.reset(tok_t)


class TestParentSearch:
    async def test_tenant_isolation_same_email_across_tenants(
        self, db: AsyncSession, two_tenants_with_parents, _as_admin_of_tenant_a
    ):
        """Alice AND Bob both have email 'duplicate@parent.test' but in
        different tenants. Admin of tenant A must only see Alice."""
        resp = await users_api.search_parents(
            q="duplicate@parent.test", limit=10, db=db,
        )
        parents = resp.data["parents"]
        ids = {p["id"] for p in parents}
        assert str(two_tenants_with_parents.parent_a.id) in ids
        assert str(two_tenants_with_parents.parent_b.id) not in ids, (
            "TENANT LEAK: search returned a parent from another tenant"
        )

    async def test_name_search_matches_first_or_last(
        self, db: AsyncSession, two_tenants_with_parents, _as_admin_of_tenant_a
    ):
        # First-name partial: "Ali" → Alice
        resp = await users_api.search_parents(q="Ali", limit=10, db=db)
        ids = {p["id"] for p in resp.data["parents"]}
        assert str(two_tenants_with_parents.parent_a.id) in ids
        assert str(two_tenants_with_parents.parent_a_2.id) not in ids

        # Last-name search: "Doe" matches both parents in tenant A.
        resp = await users_api.search_parents(q="Doe", limit=10, db=db)
        ids = {p["id"] for p in resp.data["parents"]}
        assert str(two_tenants_with_parents.parent_a.id) in ids
        assert str(two_tenants_with_parents.parent_a_2.id) in ids

    async def test_results_include_linked_children(
        self, db: AsyncSession, two_tenants_with_parents, _as_admin_of_tenant_a
    ):
        """The UX depends on seeing children in the typeahead row —
        otherwise the admin can't confirm identity in bulk-parent cases."""
        resp = await users_api.search_parents(q="Alice", limit=10, db=db)
        alice = next(
            p for p in resp.data["parents"]
            if p["id"] == str(two_tenants_with_parents.parent_a.id)
        )
        assert alice["children"], "Alice must have Sarah in the results"
        assert alice["children"][0]["first_name"] == "Sarah"
        # Also confirm the summary fields the UI renders.
        assert "email" in alice
        assert alice["first_name"] == "Alice"

    async def test_no_query_returns_all_tenant_parents(
        self, db: AsyncSession, two_tenants_with_parents, _as_admin_of_tenant_a
    ):
        """When admin first opens the picker (no query yet) the UI might
        pre-load — returning ALL tenant parents (up to limit) is
        acceptable + still tenant-isolated."""
        resp = await users_api.search_parents(q=None, limit=10, db=db)
        ids = {p["id"] for p in resp.data["parents"]}
        # Both A tenants present.
        assert str(two_tenants_with_parents.parent_a.id) in ids
        assert str(two_tenants_with_parents.parent_a_2.id) in ids
        # B never present.
        assert str(two_tenants_with_parents.parent_b.id) not in ids
