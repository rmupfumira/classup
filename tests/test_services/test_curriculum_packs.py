"""Curriculum packs — 2026-10-09.

Covers:
- The pack data layer (lookups by country, by code, Custom sentinel).
- The academic service's apply_curriculum_pack:
    - adds every pack subject to an empty tenant
    - skips subjects whose code already exists (idempotent)
    - records the chosen pack code onto tenant.settings
    - Custom seeds nothing but still records the choice
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subject, Tenant
from app.services.academic_service import get_academic_service
from app.utils.curriculum_packs import (
    CURRICULUM_PACKS,
    CUSTOM_PACK_CODE,
    get_pack,
    get_packs_for_country,
)


class TestPackDataLayer:
    def test_zw_offers_zimsec_and_cambridge(self):
        codes = [p.code for p in get_packs_for_country("ZW")]
        assert "ZW_ZIMSEC" in codes
        assert "ZW_CAMBRIDGE" in codes

    def test_za_offers_caps_ieb_and_cambridge(self):
        codes = [p.code for p in get_packs_for_country("ZA")]
        assert "ZA_CAPS" in codes
        assert "ZA_IEB" in codes
        assert "ZA_CAMBRIDGE" in codes

    def test_unknown_country_returns_empty(self):
        """Picking a country we don't have a pack for returns [] — the
        caller (API) always appends Custom on top so the admin still
        has an option."""
        assert get_packs_for_country("US") == []
        assert get_packs_for_country("") == []

    def test_get_pack_returns_none_for_custom(self):
        """Custom is a sentinel, not a real pack — None lets the service
        treat it as 'no subjects to seed, just record the choice'."""
        assert get_pack(CUSTOM_PACK_CODE) is None
        assert get_pack("") is None
        assert get_pack("made-up") is None

    def test_zimsec_pack_is_not_empty(self):
        pack = get_pack("ZW_ZIMSEC")
        assert pack is not None
        assert len(pack.subjects) > 20
        # Sanity: Shona must be in ZIMSEC
        assert any("Shona" in s.name for s in pack.subjects)

    def test_caps_pack_has_zulu_and_afrikaans(self):
        pack = get_pack("ZA_CAPS")
        assert pack is not None
        names = {s.name for s in pack.subjects}
        assert any("isiZulu" in n for n in names)
        assert any("Afrikaans" in n for n in names)

    def test_ieb_mirrors_caps_subject_names(self):
        """IEB subject NAMES match CAPS — the differentiator is
        assessment, not the subject list. Only the code prefix changes."""
        caps_names = {s.name for s in get_pack("ZA_CAPS").subjects}
        ieb_names = {s.name for s in get_pack("ZA_IEB").subjects}
        assert caps_names == ieb_names

    def test_pack_subject_codes_are_unique_within_pack(self):
        for country_packs in CURRICULUM_PACKS.values():
            for pack in country_packs.values():
                codes = [s.code for s in pack.subjects]
                assert len(codes) == len(set(codes)), (
                    f"Pack {pack.code} has duplicate subject codes"
                )


class TestApplyCurriculumPack:
    @pytest_asyncio.fixture
    async def tenant_with_country(self, db: AsyncSession, test_tenant: Tenant):
        """Reuse the standard test_tenant fixture, give it a ZW country."""
        from sqlalchemy.orm.attributes import flag_modified
        s = dict(test_tenant.settings or {})
        s["country"] = "ZW"
        test_tenant.settings = s
        flag_modified(test_tenant, "settings")
        await db.commit()
        return test_tenant

    @pytest.mark.asyncio
    async def test_apply_zimsec_seeds_every_subject(
        self, db: AsyncSession, tenant_with_country,
    ):
        service = get_academic_service()
        result = await service.apply_curriculum_pack(db, "ZW_ZIMSEC")

        pack = get_pack("ZW_ZIMSEC")
        assert result["added"] == len(pack.subjects)
        assert result["skipped"] == 0
        assert result["pack_code"] == "ZW_ZIMSEC"
        assert result["pack_name"] == "ZIMSEC"

        # And actually in the DB.
        rows = (await db.execute(
            select(Subject).where(Subject.tenant_id == tenant_with_country.id)
        )).scalars().all()
        assert len(rows) == len(pack.subjects)

    @pytest.mark.asyncio
    async def test_apply_twice_is_idempotent(
        self, db: AsyncSession, tenant_with_country,
    ):
        service = get_academic_service()
        pack = get_pack("ZW_ZIMSEC")

        first = await service.apply_curriculum_pack(db, "ZW_ZIMSEC")
        assert first["added"] == len(pack.subjects)

        second = await service.apply_curriculum_pack(db, "ZW_ZIMSEC")
        assert second["added"] == 0
        assert second["skipped"] == len(pack.subjects)

    @pytest.mark.asyncio
    async def test_apply_records_choice_on_tenant_settings(
        self, db: AsyncSession, tenant_with_country,
    ):
        service = get_academic_service()
        await service.apply_curriculum_pack(db, "ZW_ZIMSEC")

        await db.refresh(tenant_with_country)
        assert tenant_with_country.get_setting("curriculum_pack") == "ZW_ZIMSEC"

    @pytest.mark.asyncio
    async def test_apply_custom_seeds_nothing_but_records_choice(
        self, db: AsyncSession, tenant_with_country,
    ):
        service = get_academic_service()
        result = await service.apply_curriculum_pack(db, CUSTOM_PACK_CODE)
        assert result["added"] == 0
        assert result["skipped"] == 0
        assert result["pack_name"] == "Custom"

        await db.refresh(tenant_with_country)
        assert tenant_with_country.get_setting("curriculum_pack") == CUSTOM_PACK_CODE

        rows = (await db.execute(
            select(Subject).where(Subject.tenant_id == tenant_with_country.id)
        )).scalars().all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_apply_seeds_default_grading_system(
        self, db: AsyncSession, tenant_with_country,
    ):
        """Pack's default grading system lands as the tenant's default
        when they had none yet. Returned summary names it."""
        from app.models.academic import GradingSystem

        service = get_academic_service()
        result = await service.apply_curriculum_pack(db, "ZW_ZIMSEC")

        # ZIMSEC O-Level scale (2019+ scheme). Previous name was
        # "ZIMSEC Standard" with an incorrect A/B/C/D/E/F/U band
        # layout; the 2026-10-10 correction pass replaced it with
        # the real ZIMSEC O-Level A/B/C/D/E/U (6 bands, pass = C).
        assert result["grading_system_added"] == "ZIMSEC O-Level"

        rows = (await db.execute(
            select(GradingSystem).where(
                GradingSystem.tenant_id == tenant_with_country.id,
            )
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].name == "ZIMSEC O-Level"
        assert rows[0].is_default is True
        # 6 bands (A-E + U) — regression guard against reintroducing F.
        assert len(rows[0].grades) == 6
        assert rows[0].grades[0]["grade"] == "A"
        # Pass mark is C at 50-59%.
        c = next(g for g in rows[0].grades if g["grade"] == "C")
        assert c["min"] == 50 and c["max"] == 59

    @pytest.mark.asyncio
    async def test_apply_adds_pack_scale_without_overriding_default(
        self, db: AsyncSession, tenant_with_country,
    ):
        """2026-10-09 updated behaviour: the pack's grading system is
        ADDED to the tenant (so it's available for per-class inference)
        but is_default is NOT flipped when another scale is already
        the default. Admin's choice of default stands."""
        from app.models.academic import GradingSystem

        existing = GradingSystem(
            tenant_id=tenant_with_country.id,
            name="My Custom Scale",
            description="School already configured",
            is_default=True,
            is_active=True,
            grades=[{"min": 0, "max": 100, "grade": "P", "description": "Pass"}],
        )
        db.add(existing)
        await db.commit()

        service = get_academic_service()
        result = await service.apply_curriculum_pack(db, "ZW_ZIMSEC")
        # Pack's scale IS added now (previously it was skipped
        # entirely) so classes can later infer from it.
        assert result["grading_system_added"] == "ZIMSEC O-Level"

        rows = (await db.execute(
            select(GradingSystem).where(
                GradingSystem.tenant_id == tenant_with_country.id,
            ).order_by(GradingSystem.name)
        )).scalars().all()
        # Two scales now: the admin's custom one AND the pack's one.
        assert {r.name for r in rows} == {"My Custom Scale", "ZIMSEC O-Level"}
        # Admin's default was NOT clobbered.
        defaults = [r for r in rows if r.is_default]
        assert len(defaults) == 1
        assert defaults[0].name == "My Custom Scale"

    @pytest.mark.asyncio
    async def test_apply_can_switch_from_zimsec_to_cambridge(
        self, db: AsyncSession, tenant_with_country,
    ):
        """A school that initially picked ZIMSEC can later switch — the
        second apply tops up with Cambridge-only codes and the active
        pack flips to Cambridge."""
        service = get_academic_service()
        await service.apply_curriculum_pack(db, "ZW_ZIMSEC")
        await service.apply_curriculum_pack(db, "ZW_CAMBRIDGE")

        await db.refresh(tenant_with_country)
        assert tenant_with_country.get_setting("curriculum_pack") == "ZW_CAMBRIDGE"
        # Both pack prefixes present now.
        rows = (await db.execute(
            select(Subject.code).where(Subject.tenant_id == tenant_with_country.id)
        )).all()
        codes = [r[0] for r in rows]
        assert any(c.startswith("ZS-") for c in codes)   # ZIMSEC
        assert any(c.startswith("CIE-") for c in codes)  # Cambridge
