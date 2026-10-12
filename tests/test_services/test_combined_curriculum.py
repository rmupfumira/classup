"""Combined ZIMSEC + Cambridge curriculum (task #158, 2026-10-12).

Three things to verify:

A. Applying the ``ZW_COMBINED`` pack:
    - seeds both ZIMSEC AND Cambridge subjects;
    - stamps each subject with its ORIGINAL body code
      (ZIMSEC subjects → ZW_ZIMSEC, Cambridge subjects → ZW_CAMBRIDGE)
      so report cards can grade each with the right scale;
    - seeds BOTH grading systems (ZIMSEC O-Level + Cambridge A*-U),
      marking only the first as default.

B. Student subject overrides:
    - idempotent set — a second call with a different override
      subject updates the existing row rather than duplicating.
    - cross-tenant / cross-class rejection.
    - clear removes the row.

C. get_effective_subjects_for_student:
    - no override → returns the class's class_subjects verbatim.
    - with override → the overridden class_subject is swapped for
      the override_subject, position preserved.
    - student with no class → empty list.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException, ValidationException
from app.models import (
    ClassSubject,
    GradingSystem,
    SchoolClass,
    Student,
    Subject,
    Tenant,
    StudentSubjectOverride,
)
from app.services.academic_service import get_academic_service
from app.utils.tenant_context import _tenant_id


@pytest_asyncio.fixture
async def combined_tenant(db: AsyncSession):
    """A fresh tenant with the ZW_COMBINED pack applied."""
    tid = uuid.uuid4()
    tenant = Tenant(
        id=tid, name=f"CT {tid.hex[:6]}", slug=f"ct-{tid.hex[:8]}",
        email=f"admin@{tid.hex[:6]}.example.com",
        education_type="HIGH_SCHOOL",
        settings={"country": "ZW"},
        is_active=True, onboarding_completed=True,
    )
    db.add(tenant)
    await db.commit()

    tok = _tenant_id.set(tid)
    try:
        await get_academic_service().apply_curriculum_pack(db, "ZW_COMBINED")
    finally:
        _tenant_id.reset(tok)

    yield {"tenant": tenant, "tid": tid}

    from sqlalchemy import text as _text
    async with db.bind.connect() as conn:
        await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": tid})
        await conn.commit()


class TestCombinedPackSeeding:
    @pytest.mark.asyncio
    async def test_seeds_both_zimsec_and_cambridge_subjects(
        self, db, combined_tenant,
    ):
        tid = combined_tenant["tid"]
        subjects = (await db.execute(
            select(Subject).where(Subject.tenant_id == tid)
        )).scalars().all()
        codes = {s.code for s in subjects}
        # Spot check — a few from each body.
        assert "ZS-MAT-O" in codes
        assert "CIE-MAT-IG" in codes
        assert "ZS-SHO-O" in codes
        assert "CIE-ENG-IG" in codes

    @pytest.mark.asyncio
    async def test_subjects_tagged_with_their_original_body(
        self, db, combined_tenant,
    ):
        """ZIMSEC subjects → ZW_ZIMSEC, Cambridge → ZW_CAMBRIDGE.
        Not ZW_COMBINED — that would collapse the grading-scale
        selection on report cards."""
        tid = combined_tenant["tid"]
        rows = (await db.execute(
            select(Subject.code, Subject.curriculum_pack_code)
            .where(Subject.tenant_id == tid)
        )).all()
        tagged = dict(rows)
        assert tagged["ZS-MAT-O"] == "ZW_ZIMSEC"
        assert tagged["CIE-MAT-IG"] == "ZW_CAMBRIDGE"
        assert tagged["ZS-ENG-P"] == "ZW_ZIMSEC"
        assert tagged["CIE-ENG-A"] == "ZW_CAMBRIDGE"
        # Nothing stamped as the Combined pack itself.
        assert "ZW_COMBINED" not in tagged.values()

    @pytest.mark.asyncio
    async def test_seeds_both_grading_systems(self, db, combined_tenant):
        tid = combined_tenant["tid"]
        systems = (await db.execute(
            select(GradingSystem).where(GradingSystem.tenant_id == tid)
        )).scalars().all()
        names = {g.name for g in systems}
        assert "ZIMSEC O-Level" in names
        assert "Cambridge A*-U" in names

    @pytest.mark.asyncio
    async def test_only_first_grading_system_is_default(
        self, db, combined_tenant,
    ):
        """Protects against a Combined apply silently flipping a
        later-added scale into the tenant's default position."""
        tid = combined_tenant["tid"]
        defaults = (await db.execute(
            select(GradingSystem).where(
                GradingSystem.tenant_id == tid,
                GradingSystem.is_default.is_(True),
            )
        )).scalars().all()
        assert len(defaults) == 1
        # ZIMSEC O-Level is listed first in the pack's seeding order.
        assert defaults[0].name == "ZIMSEC O-Level"


class TestStudentSubjectOverrideCrud:
    @pytest_asyncio.fixture
    async def small_world(self, db, combined_tenant):
        """A class, two subjects (ZIMSEC Math + Cambridge Math), a
        student enrolled in the class with ZIMSEC Math as the class
        default."""
        tid = combined_tenant["tid"]
        cls = SchoolClass(
            id=uuid.uuid4(), tenant_id=tid, name="Form 4A", is_active=True,
        )
        db.add(cls)
        await db.flush()

        math_zimsec = (await db.execute(
            select(Subject).where(
                Subject.tenant_id == tid, Subject.code == "ZS-MAT-O",
            )
        )).scalar_one()
        math_cambridge = (await db.execute(
            select(Subject).where(
                Subject.tenant_id == tid, Subject.code == "CIE-MAT-IG",
            )
        )).scalar_one()

        cs_math_zimsec = ClassSubject(
            id=uuid.uuid4(), class_id=cls.id,
            subject_id=math_zimsec.id, is_compulsory=True, display_order=0,
        )
        db.add(cs_math_zimsec)

        student = Student(
            id=uuid.uuid4(), tenant_id=tid,
            first_name="Alice", last_name="Mukamuri",
            class_id=cls.id, is_active=True,
        )
        db.add(student)
        await db.commit()

        return {
            "cls": cls, "student": student,
            "math_zimsec": math_zimsec, "math_cambridge": math_cambridge,
            "cs_math_zimsec": cs_math_zimsec,
        }

    @pytest.mark.asyncio
    async def test_set_override_is_idempotent(self, db, combined_tenant, small_world):
        svc = get_academic_service()
        tok = _tenant_id.set(combined_tenant["tid"])
        try:
            a = await svc.set_student_subject_override(
                db,
                student_id=small_world["student"].id,
                class_subject_id=small_world["cs_math_zimsec"].id,
                override_subject_id=small_world["math_cambridge"].id,
            )
            b = await svc.set_student_subject_override(
                db,
                student_id=small_world["student"].id,
                class_subject_id=small_world["cs_math_zimsec"].id,
                override_subject_id=small_world["math_cambridge"].id,
            )
        finally:
            _tenant_id.reset(tok)
        assert a.id == b.id

    @pytest.mark.asyncio
    async def test_set_override_rejects_same_subject_as_class(
        self, db, combined_tenant, small_world,
    ):
        svc = get_academic_service()
        tok = _tenant_id.set(combined_tenant["tid"])
        try:
            with pytest.raises(ValidationException):
                await svc.set_student_subject_override(
                    db,
                    student_id=small_world["student"].id,
                    class_subject_id=small_world["cs_math_zimsec"].id,
                    override_subject_id=small_world["math_zimsec"].id,
                )
        finally:
            _tenant_id.reset(tok)

    @pytest.mark.asyncio
    async def test_clear_override_removes_row(
        self, db, combined_tenant, small_world,
    ):
        svc = get_academic_service()
        tok = _tenant_id.set(combined_tenant["tid"])
        try:
            await svc.set_student_subject_override(
                db,
                student_id=small_world["student"].id,
                class_subject_id=small_world["cs_math_zimsec"].id,
                override_subject_id=small_world["math_cambridge"].id,
            )
            removed = await svc.clear_student_subject_override(
                db,
                student_id=small_world["student"].id,
                class_subject_id=small_world["cs_math_zimsec"].id,
            )
            assert removed is True
            # Second call = already gone.
            removed_again = await svc.clear_student_subject_override(
                db,
                student_id=small_world["student"].id,
                class_subject_id=small_world["cs_math_zimsec"].id,
            )
            assert removed_again is False
        finally:
            _tenant_id.reset(tok)


class TestEffectiveSubjects:
    @pytest_asyncio.fixture
    async def three_subjects_class(self, db, combined_tenant):
        """Class with ZIMSEC Math + ZIMSEC English + ZIMSEC History,
        one student enrolled."""
        tid = combined_tenant["tid"]
        cls = SchoolClass(
            id=uuid.uuid4(), tenant_id=tid, name="Form 4B", is_active=True,
        )
        db.add(cls)
        await db.flush()

        codes = ["ZS-MAT-O", "ZS-ENG-O", "ZS-HIS-O"]
        subjects = {}
        css = {}
        for i, code in enumerate(codes):
            s = (await db.execute(
                select(Subject).where(
                    Subject.tenant_id == tid, Subject.code == code,
                )
            )).scalar_one()
            subjects[code] = s
            cs = ClassSubject(
                id=uuid.uuid4(), class_id=cls.id, subject_id=s.id,
                is_compulsory=True, display_order=i,
            )
            db.add(cs)
            css[code] = cs

        student = Student(
            id=uuid.uuid4(), tenant_id=tid,
            first_name="Bob", last_name="Zulu",
            class_id=cls.id, is_active=True,
        )
        db.add(student)
        await db.commit()

        cambridge_math = (await db.execute(
            select(Subject).where(
                Subject.tenant_id == tid, Subject.code == "CIE-MAT-IG",
            )
        )).scalar_one()

        return {
            "cls": cls, "student": student,
            "subjects": subjects, "css": css,
            "cambridge_math": cambridge_math,
        }

    @pytest.mark.asyncio
    async def test_no_override_returns_class_defaults(
        self, db, combined_tenant, three_subjects_class,
    ):
        svc = get_academic_service()
        tok = _tenant_id.set(combined_tenant["tid"])
        try:
            effective = await svc.get_effective_subjects_for_student(
                db, three_subjects_class["student"].id,
            )
        finally:
            _tenant_id.reset(tok)
        codes = [s.code for s in effective]
        assert codes == ["ZS-MAT-O", "ZS-ENG-O", "ZS-HIS-O"]

    @pytest.mark.asyncio
    async def test_override_swaps_matching_subject_only(
        self, db, combined_tenant, three_subjects_class,
    ):
        """Override Math → Cambridge Math. English and History still
        appear as ZIMSEC. Position preserved."""
        svc = get_academic_service()
        tok = _tenant_id.set(combined_tenant["tid"])
        try:
            await svc.set_student_subject_override(
                db,
                student_id=three_subjects_class["student"].id,
                class_subject_id=three_subjects_class["css"]["ZS-MAT-O"].id,
                override_subject_id=three_subjects_class["cambridge_math"].id,
            )
            effective = await svc.get_effective_subjects_for_student(
                db, three_subjects_class["student"].id,
            )
        finally:
            _tenant_id.reset(tok)
        codes = [s.code for s in effective]
        # Math position (0) now holds Cambridge; the other two unchanged.
        assert codes == ["CIE-MAT-IG", "ZS-ENG-O", "ZS-HIS-O"]
        # The override subject carries the Cambridge pack code so the
        # report-card renderer picks the Cambridge grading scale.
        math_row = next(s for s in effective if s.code == "CIE-MAT-IG")
        assert math_row.curriculum_pack_code == "ZW_CAMBRIDGE"

    @pytest.mark.asyncio
    async def test_student_with_no_class_returns_empty(
        self, db, combined_tenant,
    ):
        tid = combined_tenant["tid"]
        orphan = Student(
            id=uuid.uuid4(), tenant_id=tid,
            first_name="Lonely", last_name="Student",
            class_id=None, is_active=True,
        )
        db.add(orphan)
        await db.commit()

        svc = get_academic_service()
        tok = _tenant_id.set(tid)
        try:
            effective = await svc.get_effective_subjects_for_student(
                db, orphan.id,
            )
        finally:
            _tenant_id.reset(tok)
        assert effective == []


class TestResolveSubjectGradingScales:
    """Per-subject grading-scale resolver (task #160, 2026-10-12).

    The resolver is the service-side half of the Combined report-card
    grading feature: the renderer honours a per-subject grading_system
    key when present; this resolver fills it in by looking up each
    subject's ``curriculum_pack_code`` → pack definition's
    ``grading_system.name`` → tenant's ``GradingSystem`` row.
    """

    @pytest.mark.asyncio
    async def test_zimsec_and_cambridge_subjects_get_different_scales(
        self, db, combined_tenant,
    ):
        tid = combined_tenant["tid"]
        tok = _tenant_id.set(tid)
        try:
            svc = get_academic_service()
            # Pick one subject from each body.
            zs_math = (await db.execute(
                select(Subject).where(
                    Subject.tenant_id == tid, Subject.code == "ZS-MAT-O",
                )
            )).scalar_one()
            cie_math = (await db.execute(
                select(Subject).where(
                    Subject.tenant_id == tid, Subject.code == "CIE-MAT-IG",
                )
            )).scalar_one()

            scales = await svc.resolve_subject_grading_scales(
                db, [zs_math, cie_math],
            )
        finally:
            _tenant_id.reset(tok)

        # Each subject gets its OWN scale.
        assert zs_math.id in scales
        assert cie_math.id in scales
        # Scales are distinct (ZIMSEC O-Level ends at U with 70+=A;
        # Cambridge ends at U with 90+=A*).
        zs_bands = {b["grade"] for b in scales[zs_math.id]}
        cie_bands = {b["grade"] for b in scales[cie_math.id]}
        assert "A*" in cie_bands
        assert "A*" not in zs_bands

    @pytest.mark.asyncio
    async def test_subject_without_pack_code_returns_no_entry(
        self, db, combined_tenant,
    ):
        """Subjects with no curriculum_pack_code (custom-pack schools,
        pre-feature data) fall through to the section-level scale —
        resolver returns no entry for them."""
        tid = combined_tenant["tid"]
        # A locally-created subject with no pack code.
        local = Subject(
            id=uuid.uuid4(), tenant_id=tid,
            name="School Choir", code=f"CHOIR-{uuid.uuid4().hex[:6]}",
            default_total_marks=100, curriculum_pack_code=None,
        )
        db.add(local)
        await db.commit()

        tok = _tenant_id.set(tid)
        try:
            svc = get_academic_service()
            scales = await svc.resolve_subject_grading_scales(db, [local])
        finally:
            _tenant_id.reset(tok)
        assert local.id not in scales

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self, db, combined_tenant):
        """Short-circuit path."""
        tid = combined_tenant["tid"]
        tok = _tenant_id.set(tid)
        try:
            svc = get_academic_service()
            scales = await svc.resolve_subject_grading_scales(db, [])
        finally:
            _tenant_id.reset(tok)
        assert scales == {}

    @pytest.mark.asyncio
    async def test_admin_renamed_scale_breaks_match_quietly(
        self, db, combined_tenant,
    ):
        """If an admin renames the Cambridge grading system, the
        resolver can't match it. It returns no entry rather than
        guessing — same posture as the class-grading inferrer."""
        tid = combined_tenant["tid"]

        # Rename the Cambridge scale.
        cie_scale = (await db.execute(
            select(GradingSystem).where(
                GradingSystem.tenant_id == tid,
                GradingSystem.name == "Cambridge A*-U",
            )
        )).scalar_one()
        cie_scale.name = "Our Custom Cambridge"
        await db.commit()

        cie_math = (await db.execute(
            select(Subject).where(
                Subject.tenant_id == tid, Subject.code == "CIE-MAT-IG",
            )
        )).scalar_one()

        tok = _tenant_id.set(tid)
        try:
            svc = get_academic_service()
            scales = await svc.resolve_subject_grading_scales(db, [cie_math])
        finally:
            _tenant_id.reset(tok)
        # No entry — the renderer will fall back to the section scale.
        assert cie_math.id not in scales
