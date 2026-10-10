"""Service for managing grade levels.

Grade-level defaults are now **country-aware**: Zimbabwe uses
ECD A/B → Grade 1-7 → Form 1-6 (with ZIMSEC O-Level at Form 4 and
A-Level at Lower/Upper Six); South Africa uses Grade RRR/RR/R →
Grade 1-12 (CAPS phases: Foundation / Intermediate / Senior / FET).

For any country we don't ship a per-country catalogue for, the
``_GENERIC_*`` lists stand in — those use neutral "Grade N" labels
that work for most anglophone systems.
"""

import uuid
from datetime import datetime
from typing import List, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.grade_level import GradeLevel
from app.models.tenant import EducationType
from app.utils.tenant_context import get_tenant_id


# ---------------------------------------------------------------------------
# Zimbabwe — ECD A/B → Grade 1-7 → Form 1-6
# Primary runs Grade 1-7 (Grade 7 is a ZIMSEC exam year). Secondary is
# "Form", not "Grade". Form 4 = ZIMSEC O-Level. Lower/Upper Six = ZIMSEC
# A-Level. ECD A (3-4y) and ECD B (4-5y) replace the generic
# INFANT/TODDLER/PRESCHOOL labels, which never applied in ZW.
# ---------------------------------------------------------------------------

_ZW_ECD = [
    {"code": "ECD_A", "name": "ECD A (3-4 years)", "display_order": 1},
    {"code": "ECD_B", "name": "ECD B (4-5 years)", "display_order": 2},
]
_ZW_PRIMARY = [
    {"code": "GRADE_1", "name": "Grade 1", "display_order": 10},
    {"code": "GRADE_2", "name": "Grade 2", "display_order": 11},
    {"code": "GRADE_3", "name": "Grade 3", "display_order": 12},
    {"code": "GRADE_4", "name": "Grade 4", "display_order": 13},
    {"code": "GRADE_5", "name": "Grade 5", "display_order": 14},
    {"code": "GRADE_6", "name": "Grade 6", "display_order": 15},
    {"code": "GRADE_7", "name": "Grade 7", "display_order": 16},
]
_ZW_SECONDARY = [
    {"code": "FORM_1", "name": "Form 1", "display_order": 20},
    {"code": "FORM_2", "name": "Form 2", "display_order": 21},
    {"code": "FORM_3", "name": "Form 3", "display_order": 22},
    {"code": "FORM_4", "name": "Form 4", "display_order": 23},
    {"code": "LOWER_SIX", "name": "Lower Six (Form 5)", "display_order": 24},
    {"code": "UPPER_SIX", "name": "Upper Six (Form 6)", "display_order": 25},
]

# ---------------------------------------------------------------------------
# South Africa — Grade RRR → RR → R → Grade 1-12
# Grade R is the only DBE-compulsory pre-primary year (Foundation
# Phase); RRR and RR are the common private-ECD-sector names for
# Playgroup/Toddler and Nursery. Grades 1-9 fall under GET band
# (Foundation / Intermediate / Senior); 10-12 under FET → NSC.
# ---------------------------------------------------------------------------

_ZA_ECD = [
    {"code": "GRADE_RRR", "name": "Grade RRR (Playgroup, 2-3 years)", "display_order": 1},
    {"code": "GRADE_RR", "name": "Grade RR (Nursery, 3-4 years)", "display_order": 2},
    {"code": "GRADE_R", "name": "Grade R (Reception, 5-6 years)", "display_order": 3},
]
_ZA_PRIMARY = [
    {"code": "GRADE_1", "name": "Grade 1", "display_order": 10},
    {"code": "GRADE_2", "name": "Grade 2", "display_order": 11},
    {"code": "GRADE_3", "name": "Grade 3", "display_order": 12},
    {"code": "GRADE_4", "name": "Grade 4", "display_order": 13},
    {"code": "GRADE_5", "name": "Grade 5", "display_order": 14},
    {"code": "GRADE_6", "name": "Grade 6", "display_order": 15},
    {"code": "GRADE_7", "name": "Grade 7", "display_order": 16},
]
_ZA_SECONDARY = [
    {"code": "GRADE_8", "name": "Grade 8", "display_order": 20},
    {"code": "GRADE_9", "name": "Grade 9", "display_order": 21},
    {"code": "GRADE_10", "name": "Grade 10", "display_order": 22},
    {"code": "GRADE_11", "name": "Grade 11", "display_order": 23},
    {"code": "GRADE_12", "name": "Grade 12", "display_order": 24},
]

# ---------------------------------------------------------------------------
# Generic fallback — kept to serve tenants whose country we don't yet
# ship a localisation for. These are the pre-2026 defaults, verbatim.
# ---------------------------------------------------------------------------

_GENERIC_DAYCARE = [
    {"code": "INFANT", "name": "Infant (0-12 months)", "display_order": 1},
    {"code": "TODDLER", "name": "Toddler (1-2 years)", "display_order": 2},
    {"code": "PRESCHOOL", "name": "Preschool (3-4 years)", "display_order": 3},
    {"code": "KINDERGARTEN", "name": "Kindergarten (5-6 years)", "display_order": 4},
]
_GENERIC_PRIMARY = [
    {"code": "GRADE_R", "name": "Grade R", "display_order": 1},
    {"code": "GRADE_1", "name": "Grade 1", "display_order": 2},
    {"code": "GRADE_2", "name": "Grade 2", "display_order": 3},
    {"code": "GRADE_3", "name": "Grade 3", "display_order": 4},
    {"code": "GRADE_4", "name": "Grade 4", "display_order": 5},
    {"code": "GRADE_5", "name": "Grade 5", "display_order": 6},
    {"code": "GRADE_6", "name": "Grade 6", "display_order": 7},
    {"code": "GRADE_7", "name": "Grade 7", "display_order": 8},
]
_GENERIC_HIGH = [
    {"code": "GRADE_8", "name": "Grade 8", "display_order": 1},
    {"code": "GRADE_9", "name": "Grade 9", "display_order": 2},
    {"code": "GRADE_10", "name": "Grade 10", "display_order": 3},
    {"code": "GRADE_11", "name": "Grade 11", "display_order": 4},
    {"code": "GRADE_12", "name": "Grade 12", "display_order": 5},
]


def _grade_level_catalogue(
    education_type: EducationType, country_code: str | None,
) -> list[dict]:
    """Pick the right grade-level list for an (education_type, country) pair.

    Country-specific lists override the generic defaults. For K12 /
    COMBINED we concatenate ECD + Primary + Secondary (and dedupe by
    `code`). An unknown country falls back to the generic catalogue
    we've always shipped.
    """
    cc = (country_code or "").upper()

    # Known country overrides first.
    if cc == "ZW":
        if education_type == EducationType.DAYCARE:
            return list(_ZW_ECD)
        if education_type == EducationType.PRIMARY_SCHOOL:
            return list(_ZW_ECD) + list(_ZW_PRIMARY)
        if education_type == EducationType.HIGH_SCHOOL:
            return list(_ZW_SECONDARY)
        # K12 / COMBINED → the full ZW ladder, ECD through A-Level.
        return list(_ZW_ECD) + list(_ZW_PRIMARY) + list(_ZW_SECONDARY)

    if cc == "ZA":
        if education_type == EducationType.DAYCARE:
            return list(_ZA_ECD)
        if education_type == EducationType.PRIMARY_SCHOOL:
            return list(_ZA_ECD) + list(_ZA_PRIMARY)
        if education_type == EducationType.HIGH_SCHOOL:
            return list(_ZA_SECONDARY)
        return list(_ZA_ECD) + list(_ZA_PRIMARY) + list(_ZA_SECONDARY)

    # Generic (previous behaviour, pre-2026-10) — any other country.
    if education_type == EducationType.DAYCARE:
        return list(_GENERIC_DAYCARE)
    if education_type == EducationType.PRIMARY_SCHOOL:
        return list(_GENERIC_PRIMARY)
    if education_type == EducationType.HIGH_SCHOOL:
        return list(_GENERIC_HIGH)
    if education_type == EducationType.K12:
        return list(_GENERIC_DAYCARE) + list(_GENERIC_PRIMARY) + list(_GENERIC_HIGH)
    # COMBINED (daycare + primary) legacy value.
    return list(_GENERIC_DAYCARE) + list(_GENERIC_PRIMARY)


# Legacy constant preserved for any imports that still reference it;
# new code should call `_grade_level_catalogue(...)` with a country.
DEFAULT_GRADE_LEVELS = {
    EducationType.DAYCARE: _GENERIC_DAYCARE,
    EducationType.PRIMARY_SCHOOL: _GENERIC_PRIMARY,
    EducationType.HIGH_SCHOOL: _GENERIC_HIGH,
    EducationType.K12: _GENERIC_DAYCARE + _GENERIC_PRIMARY + _GENERIC_HIGH,
    EducationType.COMBINED: _GENERIC_DAYCARE + _GENERIC_PRIMARY,
}


class GradeLevelService:
    """Service for managing grade level configuration."""

    async def get_grade_levels(
        self,
        db: AsyncSession,
        is_active: bool | None = True,
        page: int = 1,
        page_size: int = 100,
    ) -> Tuple[List[GradeLevel], int]:
        """Get all grade levels for the current tenant."""
        tenant_id = get_tenant_id()

        # Base query
        query = select(GradeLevel).where(
            GradeLevel.tenant_id == tenant_id,
            GradeLevel.deleted_at.is_(None),
        )

        # Apply filters
        if is_active is not None:
            query = query.where(GradeLevel.is_active == is_active)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(GradeLevel.display_order, GradeLevel.name)
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        grade_levels = list(result.scalars().all())

        return grade_levels, total

    async def get_grade_level(
        self, db: AsyncSession, grade_level_id: uuid.UUID
    ) -> GradeLevel | None:
        """Get a grade level by ID."""
        tenant_id = get_tenant_id()

        query = select(GradeLevel).where(
            GradeLevel.id == grade_level_id,
            GradeLevel.tenant_id == tenant_id,
            GradeLevel.deleted_at.is_(None),
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_grade_level_by_code(
        self, db: AsyncSession, code: str
    ) -> GradeLevel | None:
        """Get a grade level by code."""
        tenant_id = get_tenant_id()

        query = select(GradeLevel).where(
            GradeLevel.code == code.upper(),
            GradeLevel.tenant_id == tenant_id,
            GradeLevel.deleted_at.is_(None),
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def create_grade_level(
        self,
        db: AsyncSession,
        name: str,
        code: str,
        description: str | None = None,
        display_order: int = 0,
    ) -> GradeLevel:
        """Create a new grade level."""
        tenant_id = get_tenant_id()

        grade_level = GradeLevel(
            tenant_id=tenant_id,
            name=name,
            code=code.upper(),
            description=description,
            display_order=display_order,
            is_active=True,
        )
        db.add(grade_level)
        await db.commit()
        await db.refresh(grade_level)
        return grade_level

    async def update_grade_level(
        self,
        db: AsyncSession,
        grade_level_id: uuid.UUID,
        **kwargs,
    ) -> GradeLevel | None:
        """Update a grade level."""
        grade_level = await self.get_grade_level(db, grade_level_id)
        if not grade_level:
            return None

        for key, value in kwargs.items():
            if hasattr(grade_level, key) and value is not None:
                if key == "code":
                    value = value.upper()
                setattr(grade_level, key, value)

        await db.commit()
        await db.refresh(grade_level)
        return grade_level

    async def delete_grade_level(
        self, db: AsyncSession, grade_level_id: uuid.UUID
    ) -> bool:
        """Soft delete a grade level."""
        grade_level = await self.get_grade_level(db, grade_level_id)
        if not grade_level:
            return False

        grade_level.deleted_at = datetime.utcnow()
        await db.commit()
        return True

    async def seed_grade_levels_for_tenant(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        education_type: str,
        country_code: str | None = None,
    ) -> List[GradeLevel]:
        """Seed default grade levels for a tenant.

        ``country_code`` picks a localised catalogue (ZW → ECD A/B +
        Form 1-6; ZA → Grade RRR/RR/R + Grade 1-12). Omitted or
        unknown falls back to the generic Grade 1-12 ladder.

        Does NOT use get_tenant_id() — this runs during tenant creation
        before the context var is set.
        """
        # Convert string to enum if needed
        if isinstance(education_type, str):
            try:
                education_type = EducationType(education_type)
            except ValueError:
                education_type = EducationType.DAYCARE

        defaults = _grade_level_catalogue(education_type, country_code)

        created = []
        for config in defaults:
            grade_level = GradeLevel(
                tenant_id=tenant_id,
                name=config["name"],
                code=config["code"],
                display_order=config["display_order"],
                is_active=True,
            )
            db.add(grade_level)
            created.append(grade_level)

        await db.commit()
        for gl in created:
            await db.refresh(gl)

        return created

    async def get_available_grade_level_templates(self) -> dict:
        """Get available grade level templates for manual selection.

        Returns templates that admins can choose from when adding grade levels.
        """
        return {
            "daycare": [
                {"code": "INFANT", "name": "Infant (0-12 months)"},
                {"code": "TODDLER", "name": "Toddler (1-2 years)"},
                {"code": "PRESCHOOL", "name": "Preschool (3-4 years)"},
                {"code": "KINDERGARTEN", "name": "Kindergarten (5-6 years)"},
                {"code": "PRE_K", "name": "Pre-Kindergarten"},
            ],
            "primary": [
                {"code": "GRADE_R", "name": "Grade R"},
                {"code": "GRADE_1", "name": "Grade 1"},
                {"code": "GRADE_2", "name": "Grade 2"},
                {"code": "GRADE_3", "name": "Grade 3"},
                {"code": "GRADE_4", "name": "Grade 4"},
                {"code": "GRADE_5", "name": "Grade 5"},
                {"code": "GRADE_6", "name": "Grade 6"},
                {"code": "GRADE_7", "name": "Grade 7"},
            ],
            "high_school": [
                {"code": "GRADE_8", "name": "Grade 8"},
                {"code": "GRADE_9", "name": "Grade 9"},
                {"code": "GRADE_10", "name": "Grade 10"},
                {"code": "GRADE_11", "name": "Grade 11"},
                {"code": "GRADE_12", "name": "Grade 12"},
            ],
            "international": [
                {"code": "YEAR_1", "name": "Year 1"},
                {"code": "YEAR_2", "name": "Year 2"},
                {"code": "YEAR_3", "name": "Year 3"},
                {"code": "YEAR_4", "name": "Year 4"},
                {"code": "YEAR_5", "name": "Year 5"},
                {"code": "YEAR_6", "name": "Year 6"},
                {"code": "YEAR_7", "name": "Year 7"},
                {"code": "YEAR_8", "name": "Year 8"},
                {"code": "YEAR_9", "name": "Year 9"},
                {"code": "YEAR_10", "name": "Year 10"},
                {"code": "YEAR_11", "name": "Year 11"},
                {"code": "YEAR_12", "name": "Year 12"},
                {"code": "YEAR_13", "name": "Year 13"},
            ],
        }


# Singleton instance
_grade_level_service: GradeLevelService | None = None


def get_grade_level_service() -> GradeLevelService:
    """Get the grade level service singleton."""
    global _grade_level_service
    if _grade_level_service is None:
        _grade_level_service = GradeLevelService()
    return _grade_level_service
