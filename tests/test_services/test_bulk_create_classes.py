"""Bulk-create classes under a grade level.

Covers:
- Letter generation: first N classes get A, B, C, ...
- Idempotent-friendly: a second call picks up at the next free suffix,
  not duplicating any existing one.
- Excel-style suffixes beyond Z: 26th class is 'AA', 27th 'AB'.
- Cross-tenant / unknown grade level raises NotFoundException.
- Capacity propagates to every created row.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException
from app.models import SchoolClass, Tenant
from app.models.grade_level import GradeLevel
from app.services.class_service import ClassService, get_class_service
from app.utils.tenant_context import _tenant_id


@pytest.fixture
def svc() -> ClassService:
    return get_class_service()


async def _mk_grade(db: AsyncSession, tenant: Tenant, name: str, code: str) -> GradeLevel:
    g = GradeLevel(
        id=uuid.uuid4(), tenant_id=tenant.id,
        name=name, code=code, display_order=1, is_active=True,
    )
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return g


class TestNextExcelSuffix:
    """Static helper — unit test it directly, no DB."""

    def test_single_letters_then_double(self):
        f = ClassService._next_excel_suffix
        assert f(0) == "A"
        assert f(1) == "B"
        assert f(25) == "Z"
        assert f(26) == "AA"
        assert f(27) == "AB"
        assert f(51) == "AZ"
        assert f(52) == "BA"
        assert f(26 * 26 + 25) == "ZZ"  # Z before switching to triple
        assert f(26 * 26 + 26) == "AAA"


class TestBulkCreate:
    @pytest.mark.asyncio
    async def test_first_batch_creates_a_through_c(
        self, db: AsyncSession, test_tenant: Tenant, svc: ClassService,
    ):
        grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
        tok = _tenant_id.set(test_tenant.id)
        try:
            created = await svc.bulk_create_classes(db, grade.id, count=3)
            await db.commit()
        finally:
            _tenant_id.reset(tok)
        assert [c.name for c in created] == ["Form 4A", "Form 4B", "Form 4C"]

    @pytest.mark.asyncio
    async def test_second_batch_skips_existing_suffixes(
        self, db: AsyncSession, test_tenant: Tenant, svc: ClassService,
    ):
        grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
        tok = _tenant_id.set(test_tenant.id)
        try:
            await svc.bulk_create_classes(db, grade.id, count=2)  # A, B
            await db.commit()
            second = await svc.bulk_create_classes(db, grade.id, count=2)  # C, D
            await db.commit()
        finally:
            _tenant_id.reset(tok)
        assert [c.name for c in second] == ["Form 4C", "Form 4D"]

    @pytest.mark.asyncio
    async def test_skips_any_existing_named_match(
        self, db: AsyncSession, test_tenant: Tenant, svc: ClassService,
    ):
        """Even if an admin manually created 'Form 4B' before using
        bulk, the next bulk call must skip B."""
        grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
        db.add(SchoolClass(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            name="Form 4B", grade_level_id=grade.id, is_active=True,
        ))
        await db.commit()
        tok = _tenant_id.set(test_tenant.id)
        try:
            created = await svc.bulk_create_classes(db, grade.id, count=3)
            await db.commit()
        finally:
            _tenant_id.reset(tok)
        # B is taken → A, C, D
        assert [c.name for c in created] == ["Form 4A", "Form 4C", "Form 4D"]

    @pytest.mark.asyncio
    async def test_capacity_propagates(
        self, db: AsyncSession, test_tenant: Tenant, svc: ClassService,
    ):
        grade = await _mk_grade(db, test_tenant, "Form 1", "FORM_1")
        tok = _tenant_id.set(test_tenant.id)
        try:
            created = await svc.bulk_create_classes(db, grade.id, count=2, capacity=30)
            await db.commit()
        finally:
            _tenant_id.reset(tok)
        for c in created:
            assert c.capacity == 30

    @pytest.mark.asyncio
    async def test_unknown_grade_level_raises(
        self, db: AsyncSession, test_tenant: Tenant, svc: ClassService,
    ):
        tok = _tenant_id.set(test_tenant.id)
        try:
            with pytest.raises(NotFoundException):
                await svc.bulk_create_classes(db, uuid.uuid4(), count=1)
        finally:
            _tenant_id.reset(tok)

    @pytest.mark.asyncio
    async def test_cross_tenant_grade_rejected(
        self, db: AsyncSession, test_tenant: Tenant, svc: ClassService,
    ):
        """A grade level on tenant A must not be usable from tenant B."""
        from app.utils.security import hash_password

        other = Tenant(
            id=uuid.uuid4(), name="Other", slug=f"other-{uuid.uuid4().hex[:6]}",
            email=f"a@{uuid.uuid4().hex[:6]}.example.com",
            education_type="HIGH_SCHOOL", is_active=True, onboarding_completed=True,
        )
        db.add(other)
        await db.commit()
        other_grade = await _mk_grade(db, other, "Form 2", "FORM_2")

        tok = _tenant_id.set(test_tenant.id)
        try:
            with pytest.raises(NotFoundException):
                await svc.bulk_create_classes(db, other_grade.id, count=1)
        finally:
            _tenant_id.reset(tok)
