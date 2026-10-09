"""Curriculum-driven class grading system — 2026-10-09.

Once a class has subjects mapped, it should pick up the grading scale
from the curriculum those subjects came from. The school admin can
pin a different scale at any time, and that override wins.

Covered:
- apply_curriculum_pack stamps each seeded Subject with its pack code.
- assign_subject_to_class on a class with no grading_system_id infers
  one from the subject's pack.
- bulk_assign_subjects_to_class does the same from the first commit.
- Mixed-curriculum classes get the DOMINANT pack's grading system.
- A class with an admin-pinned grading_system_id is NOT re-inferred.
- resolve_class_grading_system falls back from class → tenant default.
- set_class_grading_system pins and clears correctly.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolClass, Tenant
from app.models.academic import GradingSystem, Subject
from app.services.academic_service import get_academic_service
from app.utils.tenant_context import _tenant_id


@pytest_asyncio.fixture
async def tenant_with_pack(db: AsyncSession, test_tenant: Tenant):
    """Load the ZIMSEC pack into the tenant so subjects are stamped
    and the ZIMSEC Standard grading system exists."""
    tok = _tenant_id.set(test_tenant.id)
    try:
        await get_academic_service().apply_curriculum_pack(db, "ZW_ZIMSEC")
    finally:
        _tenant_id.reset(tok)
    return test_tenant


@pytest_asyncio.fixture
async def tenant_with_two_packs(db: AsyncSession, test_tenant: Tenant):
    """Load ZIMSEC + Cambridge so a class can mix subjects from both
    and we can prove the inferrer uses the majority pack."""
    tok = _tenant_id.set(test_tenant.id)
    try:
        svc = get_academic_service()
        await svc.apply_curriculum_pack(db, "ZW_ZIMSEC")
        await svc.apply_curriculum_pack(db, "ZW_CAMBRIDGE")
    finally:
        _tenant_id.reset(tok)
    return test_tenant


class TestApplyPackStampsSubjects:
    @pytest.mark.asyncio
    async def test_each_seeded_subject_carries_pack_code(
        self, db: AsyncSession, tenant_with_pack,
    ):
        rows = (await db.execute(
            select(Subject.curriculum_pack_code).where(
                Subject.tenant_id == tenant_with_pack.id,
                Subject.deleted_at.is_(None),
            )
        )).all()
        codes = {row[0] for row in rows}
        # Every ZIMSEC-seeded subject carries the pack code.
        assert codes == {"ZW_ZIMSEC"}


class TestInferOnAssign:
    @pytest.mark.asyncio
    async def test_class_with_no_grading_gets_pack_default(
        self, db: AsyncSession, tenant_with_pack, test_class: SchoolClass,
    ):
        # test_class comes from conftest; it has no grading_system_id
        assert test_class.grading_system_id is None

        # Grab one ZIMSEC subject
        subject = (await db.execute(
            select(Subject).where(
                Subject.tenant_id == tenant_with_pack.id,
                Subject.curriculum_pack_code == "ZW_ZIMSEC",
            ).limit(1)
        )).scalar_one()

        tok = _tenant_id.set(tenant_with_pack.id)
        try:
            await get_academic_service().assign_subject_to_class(
                db, test_class.id, subject.id,
            )
        finally:
            _tenant_id.reset(tok)

        await db.refresh(test_class)
        assert test_class.grading_system_id is not None

        gs = await db.get(GradingSystem, test_class.grading_system_id)
        assert gs.name == "ZIMSEC Standard"

    @pytest.mark.asyncio
    async def test_bulk_assign_also_infers(
        self, db: AsyncSession, tenant_with_pack, test_class: SchoolClass,
    ):
        assert test_class.grading_system_id is None

        subject_ids = [row[0] for row in (await db.execute(
            select(Subject.id).where(
                Subject.tenant_id == tenant_with_pack.id,
                Subject.curriculum_pack_code == "ZW_ZIMSEC",
            ).limit(3)
        )).all()]

        tok = _tenant_id.set(tenant_with_pack.id)
        try:
            await get_academic_service().bulk_assign_subjects_to_class(
                db, test_class.id, subject_ids,
            )
        finally:
            _tenant_id.reset(tok)

        await db.refresh(test_class)
        assert test_class.grading_system_id is not None
        gs = await db.get(GradingSystem, test_class.grading_system_id)
        assert gs.name == "ZIMSEC Standard"

    @pytest.mark.asyncio
    async def test_majority_pack_wins_on_mixed_class(
        self, db: AsyncSession, tenant_with_two_packs, test_class: SchoolClass,
    ):
        """Class with 2 ZIMSEC + 3 Cambridge subjects → class picks
        the Cambridge A*-U grading system (majority)."""
        zim_ids = [row[0] for row in (await db.execute(
            select(Subject.id).where(
                Subject.tenant_id == tenant_with_two_packs.id,
                Subject.curriculum_pack_code == "ZW_ZIMSEC",
            ).limit(2)
        )).all()]
        cam_ids = [row[0] for row in (await db.execute(
            select(Subject.id).where(
                Subject.tenant_id == tenant_with_two_packs.id,
                Subject.curriculum_pack_code == "ZW_CAMBRIDGE",
            ).limit(3)
        )).all()]

        tok = _tenant_id.set(tenant_with_two_packs.id)
        try:
            await get_academic_service().bulk_assign_subjects_to_class(
                db, test_class.id, zim_ids + cam_ids,
            )
        finally:
            _tenant_id.reset(tok)

        await db.refresh(test_class)
        gs = await db.get(GradingSystem, test_class.grading_system_id)
        assert gs.name == "Cambridge A*-U"

    @pytest.mark.asyncio
    async def test_pinned_class_grading_is_not_overridden(
        self, db: AsyncSession, tenant_with_two_packs, test_class: SchoolClass,
    ):
        """Admin pinned a specific scale on this class → the inferrer
        must leave it alone, even if later subjects would suggest a
        different one."""
        tok = _tenant_id.set(tenant_with_two_packs.id)
        svc = get_academic_service()
        try:
            # Pin ZIMSEC Standard manually
            zim_gs = (await db.execute(
                select(GradingSystem).where(
                    GradingSystem.tenant_id == tenant_with_two_packs.id,
                    GradingSystem.name == "ZIMSEC Standard",
                )
            )).scalar_one()
            await svc.set_class_grading_system(db, test_class.id, zim_gs.id)

            # Now assign only Cambridge subjects
            cam_ids = [row[0] for row in (await db.execute(
                select(Subject.id).where(
                    Subject.tenant_id == tenant_with_two_packs.id,
                    Subject.curriculum_pack_code == "ZW_CAMBRIDGE",
                ).limit(3)
            )).all()]
            await svc.bulk_assign_subjects_to_class(
                db, test_class.id, cam_ids,
            )
        finally:
            _tenant_id.reset(tok)

        await db.refresh(test_class)
        gs = await db.get(GradingSystem, test_class.grading_system_id)
        # Pin held — still ZIMSEC, not Cambridge.
        assert gs.name == "ZIMSEC Standard"


class TestResolve:
    @pytest.mark.asyncio
    async def test_falls_back_to_tenant_default_when_class_has_none(
        self, db: AsyncSession, tenant_with_pack, test_class: SchoolClass,
    ):
        # test_class has no grading system; the tenant's default came
        # from the ZIMSEC pack (apply made it is_default=True since the
        # tenant had none before).
        tok = _tenant_id.set(tenant_with_pack.id)
        try:
            gs = await get_academic_service().resolve_class_grading_system(
                db, test_class.id,
            )
        finally:
            _tenant_id.reset(tok)
        assert gs is not None
        assert gs.name == "ZIMSEC Standard"

    @pytest.mark.asyncio
    async def test_class_pin_wins_over_tenant_default(
        self, db: AsyncSession, tenant_with_two_packs, test_class: SchoolClass,
    ):
        """Tenant default from first-applied pack is ZIMSEC, but if the
        admin pinned Cambridge on this class, resolve returns Cambridge."""
        tok = _tenant_id.set(tenant_with_two_packs.id)
        svc = get_academic_service()
        try:
            cam_gs = (await db.execute(
                select(GradingSystem).where(
                    GradingSystem.tenant_id == tenant_with_two_packs.id,
                    GradingSystem.name == "Cambridge A*-U",
                )
            )).scalar_one()
            await svc.set_class_grading_system(db, test_class.id, cam_gs.id)

            resolved = await svc.resolve_class_grading_system(db, test_class.id)
        finally:
            _tenant_id.reset(tok)
        assert resolved.name == "Cambridge A*-U"


class TestSetAndClear:
    @pytest.mark.asyncio
    async def test_set_pins_grading_system(
        self, db: AsyncSession, tenant_with_pack, test_class: SchoolClass,
    ):
        tok = _tenant_id.set(tenant_with_pack.id)
        svc = get_academic_service()
        try:
            gs = (await db.execute(
                select(GradingSystem).where(
                    GradingSystem.tenant_id == tenant_with_pack.id,
                )
            )).scalars().first()
            ok = await svc.set_class_grading_system(db, test_class.id, gs.id)
        finally:
            _tenant_id.reset(tok)
        assert ok is True
        await db.refresh(test_class)
        assert test_class.grading_system_id == gs.id

    @pytest.mark.asyncio
    async def test_clear_removes_pin(
        self, db: AsyncSession, tenant_with_pack, test_class: SchoolClass,
    ):
        tok = _tenant_id.set(tenant_with_pack.id)
        svc = get_academic_service()
        try:
            gs = (await db.execute(
                select(GradingSystem).where(
                    GradingSystem.tenant_id == tenant_with_pack.id,
                )
            )).scalars().first()
            await svc.set_class_grading_system(db, test_class.id, gs.id)
            await svc.set_class_grading_system(db, test_class.id, None)
        finally:
            _tenant_id.reset(tok)
        await db.refresh(test_class)
        assert test_class.grading_system_id is None

    @pytest.mark.asyncio
    async def test_set_rejects_cross_tenant_grading_system(
        self, db: AsyncSession, tenant_with_pack, test_class: SchoolClass,
    ):
        """Admin can't link a class to a grading system that belongs
        to a different tenant."""
        # Create a second tenant + grading system
        from app.models.tenant import EducationType
        from app.services.tenant_service import get_tenant_service
        other = await get_tenant_service().create_tenant(
            db,
            name=f"Other {uuid.uuid4().hex[:6]}",
            email=f"o-{uuid.uuid4().hex[:6]}@example.com",
            education_type=EducationType.PRIMARY_SCHOOL,
        )
        try:
            foreign_gs = GradingSystem(
                tenant_id=other.id,
                name="Foreign",
                description="",
                is_default=False,
                is_active=True,
                grades=[{"min": 0, "max": 100, "grade": "X", "description": "x"}],
            )
            db.add(foreign_gs)
            await db.commit()

            tok = _tenant_id.set(tenant_with_pack.id)
            try:
                ok = await get_academic_service().set_class_grading_system(
                    db, test_class.id, foreign_gs.id,
                )
            finally:
                _tenant_id.reset(tok)
            assert ok is False
        finally:
            # Clean up the other tenant
            from sqlalchemy import text as _text
            async with db.bind.connect() as conn:
                await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": other.id})
                await conn.commit()
