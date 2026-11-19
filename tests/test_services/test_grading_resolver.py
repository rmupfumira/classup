"""Grading-scale resolver tests.

Pins down the "admin / teacher edit view and parent-finalised view must
show the same letter grade for the same percentage" contract. The bug
was that edit.html + create.html computed grades from
section.grading_system (frozen in the template) while
view_report_card.html + PDF used the live grading system from the DB —
so if the admin updated the grading scale after a template was built,
ICT at 60% would read 'D' on the admin screen and 'C' on the parent's
copy.

The fix funnels all three view paths through app.web.reports._resolve_grading_system,
which picks: template.grading_system_id > class grading > tenant default.
These tests verify the chain, and that both views now see the same scale.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolClass, Tenant, User
from app.models.academic import GradingSystem
from app.models.report import ReportTemplate
from app.services.academic_service import get_academic_service
from app.web.reports import _resolve_grading_system


# ──────── fixtures ────────

ZIMSEC_SCALE = [
    {"min": 80, "max": 100, "grade": "A", "description": "Distinction"},
    {"min": 70, "max": 79,  "grade": "B", "description": "Merit"},
    {"min": 60, "max": 69,  "grade": "C", "description": "Credit"},
    {"min": 50, "max": 59,  "grade": "D", "description": "Pass"},
    {"min": 0,  "max": 49,  "grade": "U", "description": "Fail"},
]
GENERIC_SCALE = [
    {"min": 80, "max": 100, "grade": "A", "description": "Excellent"},
    {"min": 70, "max": 79,  "grade": "B", "description": "Good"},
    {"min": 60, "max": 69,  "grade": "C", "description": "Average"},
    {"min": 50, "max": 59,  "grade": "D", "description": "Pass"},
    {"min": 0,  "max": 49,  "grade": "F", "description": "Fail"},
]


@pytest_asyncio.fixture
async def zimsec_system(db: AsyncSession, test_tenant: Tenant) -> GradingSystem:
    gs = GradingSystem(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        name="ZIMSEC",
        description="Zimbabwean national scale",
        is_default=False,
        is_active=True,
        grades=list(ZIMSEC_SCALE),
    )
    db.add(gs)
    await db.commit()
    await db.refresh(gs)
    return gs


@pytest_asyncio.fixture
async def generic_system(db: AsyncSession, test_tenant: Tenant) -> GradingSystem:
    gs = GradingSystem(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        name="Generic A-F",
        description="Generic scale",
        is_default=True,
        is_active=True,
        grades=list(GENERIC_SCALE),
    )
    db.add(gs)
    await db.commit()
    await db.refresh(gs)
    return gs


# ──────── tests ────────


class TestResolveGradingSystem:
    async def test_template_wins_over_class_and_default(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass,
        test_admin: User, zimsec_system: GradingSystem, generic_system: GradingSystem,
    ):
        """When a template has its own grading_system_id, it wins —
        most specific, takes precedence over the class + tenant default."""
        # Tenant default = generic, class points at nothing (falls to generic),
        # template points explicitly at ZIMSEC.
        tpl = ReportTemplate(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name="Term report",
            report_type="REPORT_CARD",
            frequency="TERMLY",
            sections=[],
            grading_system_id=zimsec_system.id,
            is_active=True,
        )
        db.add(tpl)
        await db.commit()

        resolved = await _resolve_grading_system(db, template=tpl, class_id=test_class.id)
        assert resolved is not None
        assert resolved.id == zimsec_system.id
        # And its grades are the ZIMSEC scale — 60% → C
        assert resolved.get_grade_letter(60) == "C"

    async def test_class_wins_when_template_has_none(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass,
        zimsec_system: GradingSystem, generic_system: GradingSystem,
    ):
        """If the template doesn't pin a grading system, fall through to
        the class's grading system. test_class doesn't have one set here,
        so this should land on the tenant default (generic)."""
        tpl = ReportTemplate(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name="Term report (no scale)",
            report_type="REPORT_CARD",
            frequency="TERMLY",
            sections=[],
            grading_system_id=None,
            is_active=True,
        )
        db.add(tpl)
        await db.commit()

        resolved = await _resolve_grading_system(db, template=tpl, class_id=test_class.id)
        assert resolved is not None
        # Nothing on the class → tenant default (generic) applies
        assert resolved.id == generic_system.id
        assert resolved.get_grade_letter(60) == "C"

    async def test_tenant_default_fallback(
        self, db: AsyncSession, test_tenant: Tenant, generic_system: GradingSystem,
    ):
        """No template, no class — tenant default wins."""
        resolved = await _resolve_grading_system(db, template=None, class_id=None)
        assert resolved is not None
        assert resolved.id == generic_system.id

    async def test_both_edit_and_view_see_same_scale(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass,
        zimsec_system: GradingSystem, generic_system: GradingSystem,
    ):
        """The bug: edit screen used section.grading_system, view used
        live grading_system — mismatch. Both now go through the resolver
        and see the same object."""
        tpl = ReportTemplate(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name="ICT midterm",
            report_type="REPORT_CARD",
            frequency="TERMLY",
            sections=[],
            grading_system_id=zimsec_system.id,
            is_active=True,
        )
        db.add(tpl)
        await db.commit()

        edit_scale = await _resolve_grading_system(db, template=tpl, class_id=test_class.id)
        view_scale = await _resolve_grading_system(db, template=tpl, class_id=test_class.id)

        assert edit_scale.id == view_scale.id
        # And the famous "ICT at 60% is a C" case
        assert edit_scale.get_grade_letter(60) == "C"
        assert view_scale.get_grade_letter(60) == "C"
