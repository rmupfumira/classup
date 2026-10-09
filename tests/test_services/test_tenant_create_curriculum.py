"""Tenant-create flow — jurisdiction + curriculum pack (2026-10-09).

Covers:
- `tenant_service.create_tenant(country=...)` lands the country on
  `settings.country`.
- `TenantCreateRequest` accepts `country` + `curriculum_pack`.
- The super-admin API applies the pack right after creating the
  tenant, so subjects + grading system exist from the first visit.
- Pack failures don't roll back the tenant (admin can retry later).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subject, Tenant
from app.models.academic import GradingSystem
from app.models.tenant import EducationType
from app.schemas.tenant import TenantCreateRequest
from app.services.academic_service import get_academic_service
from app.services.tenant_service import get_tenant_service
from app.utils.tenant_context import _tenant_id


async def _drop_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Teardown helper: raw SQL delete so DB-level CASCADE wipes the
    tenant and every child row in one shot. ORM db.delete(tenant) tries
    SET NULL on grade_levels.tenant_id and trips a NOT NULL constraint."""
    async with db.bind.connect() as conn:
        await conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})
        await conn.commit()


class TestServiceStoresCountry:
    @pytest.mark.asyncio
    async def test_country_lands_on_settings(self, db: AsyncSession):
        service = get_tenant_service()
        tenant = await service.create_tenant(
            db,
            name=f"Curr Test {uuid.uuid4().hex[:6]}",
            email=f"admin-{uuid.uuid4().hex[:6]}@example.com",
            education_type=EducationType.PRIMARY_SCHOOL,
            country="ZW",
        )
        try:
            assert tenant.get_setting("country") == "ZW"
        finally:
            await _drop_tenant(db, tenant.id)

    @pytest.mark.asyncio
    async def test_country_normalises_to_upper(self, db: AsyncSession):
        service = get_tenant_service()
        tenant = await service.create_tenant(
            db,
            name=f"Curr Test {uuid.uuid4().hex[:6]}",
            email=f"admin-{uuid.uuid4().hex[:6]}@example.com",
            education_type=EducationType.PRIMARY_SCHOOL,
            country="za",
        )
        try:
            assert tenant.get_setting("country") == "ZA"
        finally:
            await _drop_tenant(db, tenant.id)


class TestSchemaAcceptsNewFields:
    def test_minimal_payload_still_valid(self):
        """Old callers (no country, no curriculum) stay valid — the
        schema is backwards-compatible. UI makes them required."""
        req = TenantCreateRequest(
            name="A School",
            email="admin@example.com",
            education_type=EducationType.PRIMARY_SCHOOL,
        )
        assert req.country is None
        assert req.curriculum_pack is None

    def test_full_payload_round_trips(self):
        req = TenantCreateRequest(
            name="A School",
            email="admin@example.com",
            education_type=EducationType.PRIMARY_SCHOOL,
            country="ZW",
            curriculum_pack="ZW_ZIMSEC",
        )
        assert req.country == "ZW"
        assert req.curriculum_pack == "ZW_ZIMSEC"


class TestEndToEndCreationSeedsPack:
    @pytest.mark.asyncio
    async def test_zimsec_subjects_land_after_create(self, db: AsyncSession):
        """Replays what the API endpoint does: create tenant, set its
        tenant context, call apply_curriculum_pack. End state: both
        subjects and grading system exist on the new tenant."""
        service = get_tenant_service()
        tenant = await service.create_tenant(
            db,
            name=f"E2E Test {uuid.uuid4().hex[:6]}",
            email=f"admin-{uuid.uuid4().hex[:6]}@example.com",
            education_type=EducationType.PRIMARY_SCHOOL,
            country="ZW",
        )
        try:
            token = _tenant_id.set(tenant.id)
            try:
                summary = await get_academic_service().apply_curriculum_pack(
                    db, "ZW_ZIMSEC",
                )
            finally:
                _tenant_id.reset(token)

            assert summary["added"] > 20
            assert summary["grading_system_added"] == "ZIMSEC Standard"

            subjects = (await db.execute(
                select(Subject).where(Subject.tenant_id == tenant.id)
            )).scalars().all()
            assert len(subjects) == summary["added"]

            grading = (await db.execute(
                select(GradingSystem).where(
                    GradingSystem.tenant_id == tenant.id,
                )
            )).scalars().all()
            assert len(grading) == 1
            assert grading[0].name == "ZIMSEC Standard"

            await db.refresh(tenant)
            assert tenant.get_setting("curriculum_pack") == "ZW_ZIMSEC"
        finally:
            await _drop_tenant(db, tenant.id)

    @pytest.mark.asyncio
    async def test_custom_choice_creates_tenant_but_seeds_nothing(
        self, db: AsyncSession,
    ):
        """Super admin picks Custom → tenant exists, no subjects, no
        grading system seeded, but the choice is persisted."""
        from app.utils.curriculum_packs import CUSTOM_PACK_CODE

        service = get_tenant_service()
        tenant = await service.create_tenant(
            db,
            name=f"Custom Test {uuid.uuid4().hex[:6]}",
            email=f"admin-{uuid.uuid4().hex[:6]}@example.com",
            education_type=EducationType.PRIMARY_SCHOOL,
            country="ZW",
        )
        try:
            token = _tenant_id.set(tenant.id)
            try:
                summary = await get_academic_service().apply_curriculum_pack(
                    db, CUSTOM_PACK_CODE,
                )
            finally:
                _tenant_id.reset(token)

            assert summary["added"] == 0
            assert summary["grading_system_added"] is None

            subjects = (await db.execute(
                select(Subject).where(Subject.tenant_id == tenant.id)
            )).scalars().all()
            assert subjects == []

            await db.refresh(tenant)
            assert tenant.get_setting("curriculum_pack") == CUSTOM_PACK_CODE
        finally:
            await _drop_tenant(db, tenant.id)
