"""Grade-level ↔ subject links + auto-propagation to classes.

Covers:
- ``set_grade_subjects`` idempotent-replace semantics
- Add diff → class_subjects rows auto-created for existing classes
- Remove diff → class_subjects rows cascaded (deleted) across classes
- Cross-tenant guard: subject on tenant A can't link to a grade on tenant B
- ``create_class`` under a grade with N subjects → N class_subjects rows
- ``bulk_create_classes`` propagates subjects to every created class
- Grade with 0 linked subjects → create_class leaves class_subjects empty
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException
from app.models import (
    ClassSubject,
    GradeLevelSubject,
    SchoolClass,
    Subject,
    Tenant,
)
from app.models.grade_level import GradeLevel
from app.schemas.school_class import SchoolClassCreate
from app.services.academic_service import get_academic_service
from app.services.class_service import get_class_service
from app.utils.tenant_context import _tenant_id


async def _mk_grade(db: AsyncSession, tenant: Tenant, name: str, code: str) -> GradeLevel:
    g = GradeLevel(
        id=uuid.uuid4(), tenant_id=tenant.id,
        name=name, code=code, display_order=1, is_active=True,
    )
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return g


async def _mk_subject(db: AsyncSession, tenant: Tenant, name: str, code: str) -> Subject:
    s = Subject(
        id=uuid.uuid4(), tenant_id=tenant.id,
        name=name, code=code, is_active=True,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _mk_class(
    db: AsyncSession, tenant: Tenant, name: str, grade_level_id: uuid.UUID,
) -> SchoolClass:
    cls = SchoolClass(
        id=uuid.uuid4(), tenant_id=tenant.id,
        name=name, grade_level_id=grade_level_id, is_active=True,
    )
    db.add(cls)
    await db.commit()
    await db.refresh(cls)
    return cls


async def _class_subject_count(db: AsyncSession, class_id: uuid.UUID) -> int:
    rows = (await db.execute(
        select(ClassSubject).where(ClassSubject.class_id == class_id)
    )).scalars().all()
    return len(rows)


class TestSetGradeSubjects:
    """``AcademicService.set_grade_subjects`` idempotent-replace diff."""

    @pytest.mark.asyncio
    async def test_adds_all_on_empty_grade(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            s1 = await _mk_subject(db, test_tenant, "Mathematics", "MATH")
            s2 = await _mk_subject(db, test_tenant, "English", "ENG")
            svc = get_academic_service()
            summary = await svc.set_grade_subjects(db, grade.id, [s1.id, s2.id])
            await db.commit()
        finally:
            _tenant_id.reset(tok)
        assert summary["added"] == 2
        assert summary["removed"] == 0
        # No classes under this grade yet, so nothing to propagate.
        assert summary["propagated_to_classes"] == 0

    @pytest.mark.asyncio
    async def test_replay_same_set_is_noop(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            s1 = await _mk_subject(db, test_tenant, "Mathematics", "MATH")
            s2 = await _mk_subject(db, test_tenant, "English", "ENG")
            svc = get_academic_service()
            await svc.set_grade_subjects(db, grade.id, [s1.id, s2.id])
            await db.commit()
            summary = await svc.set_grade_subjects(db, grade.id, [s1.id, s2.id])
            await db.commit()
        finally:
            _tenant_id.reset(tok)
        assert summary == {"added": 0, "removed": 0, "propagated_to_classes": 0}

    @pytest.mark.asyncio
    async def test_partial_overlap_diffs_correctly(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """Start: {A, B}. Replace with {A, C}. Expect added=1, removed=1."""
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            a = await _mk_subject(db, test_tenant, "A", "A1")
            b = await _mk_subject(db, test_tenant, "B", "B1")
            c = await _mk_subject(db, test_tenant, "C", "C1")
            svc = get_academic_service()
            await svc.set_grade_subjects(db, grade.id, [a.id, b.id])
            await db.commit()
            summary = await svc.set_grade_subjects(db, grade.id, [a.id, c.id])
            await db.commit()
        finally:
            _tenant_id.reset(tok)
        assert summary["added"] == 1
        assert summary["removed"] == 1

        # Verify end state
        subjects = await get_academic_service().get_subjects_for_grade(db, grade.id)
        assert {s.id for s in subjects} == {a.id, c.id}


class TestCascadeToClasses:
    """set_grade_subjects cascades to existing classes under the grade."""

    @pytest.mark.asyncio
    async def test_drop_subject_removes_from_existing_classes(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """Grade has subject X and three classes, each with X attached.
        Dropping X from the grade removes the three class_subjects rows."""
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            x = await _mk_subject(db, test_tenant, "X", "X1")
            svc = get_academic_service()
            # Link X to the grade, then create 3 classes which auto-get X.
            await svc.set_grade_subjects(db, grade.id, [x.id])
            await db.commit()
            class_svc = get_class_service()
            classes = []
            for name in ("Form 4A", "Form 4B", "Form 4C"):
                data = SchoolClassCreate(name=name, grade_level_id=grade.id)
                c = await class_svc.create_class(db, data)
                await db.commit()
                classes.append(c)
            # Sanity: each class has X.
            for c in classes:
                assert await _class_subject_count(db, c.id) == 1

            # Drop X from the grade → cascade deletes 3 class_subjects rows.
            summary = await svc.set_grade_subjects(db, grade.id, [])
            await db.commit()
            assert summary["removed"] == 1
            for c in classes:
                assert await _class_subject_count(db, c.id) == 0
        finally:
            _tenant_id.reset(tok)

    @pytest.mark.asyncio
    async def test_add_subject_attaches_to_existing_classes(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """Grade has 2 pre-existing classes, no subjects linked. Add Y
        → 2 class_subjects rows created."""
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            # Make classes BEFORE any grade-subject link, so the auto-
            # attach-on-create path doesn't do the work for us.
            classes = []
            for name in ("Form 4A", "Form 4B"):
                c = await _mk_class(db, test_tenant, name, grade.id)
                classes.append(c)
            for c in classes:
                assert await _class_subject_count(db, c.id) == 0

            y = await _mk_subject(db, test_tenant, "Y", "Y1")
            svc = get_academic_service()
            summary = await svc.set_grade_subjects(db, grade.id, [y.id])
            await db.commit()
            assert summary["added"] == 1
            assert summary["propagated_to_classes"] == 2
            for c in classes:
                assert await _class_subject_count(db, c.id) == 1
        finally:
            _tenant_id.reset(tok)

    @pytest.mark.asyncio
    async def test_add_subject_idempotent_when_class_already_has_it(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """One class already has subject S attached manually. Linking S
        to the grade must not try to double-insert on that class.
        propagated_to_classes counts only the fresh rows."""
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            s = await _mk_subject(db, test_tenant, "S", "S1")
            c_pre = await _mk_class(db, test_tenant, "Form 4A", grade.id)
            c_fresh = await _mk_class(db, test_tenant, "Form 4B", grade.id)
            # Manually attach S to c_pre only.
            db.add(ClassSubject(
                class_id=c_pre.id, subject_id=s.id,
                is_compulsory=True, display_order=0,
            ))
            await db.commit()

            svc = get_academic_service()
            summary = await svc.set_grade_subjects(db, grade.id, [s.id])
            await db.commit()
            assert summary["added"] == 1
            assert summary["propagated_to_classes"] == 1  # only c_fresh gained a row
            # c_pre still has exactly 1 (not 2), c_fresh now has 1.
            assert await _class_subject_count(db, c_pre.id) == 1
            assert await _class_subject_count(db, c_fresh.id) == 1
        finally:
            _tenant_id.reset(tok)


class TestCrossTenantGuard:
    @pytest.mark.asyncio
    async def test_subject_on_tenant_a_rejected_for_grade_on_tenant_b(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """A subject that belongs to another tenant must not be linkable
        to a grade level on the current tenant."""
        other = Tenant(
            id=uuid.uuid4(), name="Other", slug=f"other-{uuid.uuid4().hex[:6]}",
            email=f"a@{uuid.uuid4().hex[:6]}.example.com",
            education_type="HIGH_SCHOOL", is_active=True, onboarding_completed=True,
        )
        db.add(other)
        await db.commit()

        # Make a subject on the OTHER tenant.
        tok = _tenant_id.set(other.id)
        try:
            foreign_subject = await _mk_subject(db, other, "Foreign", "F1")
        finally:
            _tenant_id.reset(tok)

        # Now operating as test_tenant, try to link foreign_subject to a grade here.
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            svc = get_academic_service()
            with pytest.raises(NotFoundException):
                await svc.set_grade_subjects(db, grade.id, [foreign_subject.id])
        finally:
            _tenant_id.reset(tok)


class TestCreateClassAutoAttach:
    @pytest.mark.asyncio
    async def test_create_class_under_grade_with_4_subjects(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            subs = []
            for code in ("MATH", "ENG", "SCI", "HIST"):
                subs.append(await _mk_subject(db, test_tenant, code.title(), code))
            svc = get_academic_service()
            await svc.set_grade_subjects(db, grade.id, [s.id for s in subs])
            await db.commit()

            class_svc = get_class_service()
            data = SchoolClassCreate(name="Form 4A", grade_level_id=grade.id)
            cls = await class_svc.create_class(db, data)
            await db.commit()

            assert await _class_subject_count(db, cls.id) == 4
        finally:
            _tenant_id.reset(tok)

    @pytest.mark.asyncio
    async def test_grade_with_zero_subjects_create_class_empty(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """Grade has no subjects linked yet → class is created with no
        class_subjects rows (and no error)."""
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 1", "FORM_1")
            class_svc = get_class_service()
            data = SchoolClassCreate(name="Form 1A", grade_level_id=grade.id)
            cls = await class_svc.create_class(db, data)
            await db.commit()
            assert await _class_subject_count(db, cls.id) == 0
        finally:
            _tenant_id.reset(tok)

    @pytest.mark.asyncio
    async def test_bulk_create_three_classes_with_four_subjects(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """bulk_create_classes(count=3) under a grade with 4 subjects
        → 3 × 4 = 12 class_subjects rows."""
        tok = _tenant_id.set(test_tenant.id)
        try:
            grade = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            subs = []
            for code in ("MATH", "ENG", "SCI", "HIST"):
                subs.append(await _mk_subject(db, test_tenant, code.title(), code))
            svc = get_academic_service()
            await svc.set_grade_subjects(db, grade.id, [s.id for s in subs])
            await db.commit()

            class_svc = get_class_service()
            created = await class_svc.bulk_create_classes(db, grade.id, count=3)
            await db.commit()
            assert len(created) == 3
            total = 0
            for c in created:
                total += await _class_subject_count(db, c.id)
            assert total == 12
        finally:
            _tenant_id.reset(tok)


class TestSetSubjectGrades:
    """Symmetric sibling — same cascade semantics in reverse."""

    @pytest.mark.asyncio
    async def test_set_subject_grades_cascades_to_classes(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        tok = _tenant_id.set(test_tenant.id)
        try:
            g4 = await _mk_grade(db, test_tenant, "Form 4", "FORM_4")
            g5 = await _mk_grade(db, test_tenant, "Form 5", "FORM_5")
            # One pre-existing class under each grade.
            c4 = await _mk_class(db, test_tenant, "Form 4A", g4.id)
            c5 = await _mk_class(db, test_tenant, "Form 5A", g5.id)

            maths = await _mk_subject(db, test_tenant, "Mathematics", "MATH")

            svc = get_academic_service()
            summary = await svc.set_subject_grades(db, maths.id, [g4.id, g5.id])
            await db.commit()
            assert summary["added"] == 2
            assert summary["propagated_to_classes"] == 2
            assert await _class_subject_count(db, c4.id) == 1
            assert await _class_subject_count(db, c5.id) == 1

            # Now drop g5 — c5 loses the subject, c4 keeps it.
            summary = await svc.set_subject_grades(db, maths.id, [g4.id])
            await db.commit()
            assert summary["removed"] == 1
            assert await _class_subject_count(db, c4.id) == 1
            assert await _class_subject_count(db, c5.id) == 0
        finally:
            _tenant_id.reset(tok)
