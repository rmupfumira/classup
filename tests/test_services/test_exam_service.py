"""Tests for the exam service: create → schedule → mark → finalise.

Pins the two behaviours that matter most: that marking a paper
computes percentage + grade letter from the class's grading scale,
and that finalising writes marks into the student's end-of-term
report card under the ACADEMIC_GRADES section.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolClass, Student, Tenant, User
from app.models.academic import ClassSubject, GradingSystem, Subject
from app.models.exam import Exam, ExamResult, ExamStatus, ExamSubject
from app.models.report import DailyReport, ReportTemplate
from app.services.exam_service import get_exam_service


ZIMSEC_BANDS = [
    {"min": 70, "max": 100, "grade": "A", "description": "Distinction"},
    {"min": 60, "max": 69,  "grade": "B", "description": "Merit"},
    {"min": 50, "max": 59,  "grade": "C", "description": "Pass"},
    {"min": 45, "max": 49,  "grade": "D", "description": "Lower Pass"},
    {"min": 40, "max": 44,  "grade": "E", "description": "Marginal Pass"},
    {"min": 0,  "max": 39,  "grade": "U", "description": "Ungraded"},
]


@pytest_asyncio.fixture
async def exam_seed(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, test_admin: User,
):
    """Minimal setup: a ZIMSEC scale, two subjects attached to the class,
    one report template with an ACADEMIC_GRADES section, two students.
    """
    gs = GradingSystem(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="ZIMSEC", description="", is_default=True, is_active=True,
        grades=list(ZIMSEC_BANDS),
    )
    eng = Subject(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="English", code="ENG", default_total_marks=100,
        category="SECONDARY_O", display_order=1, is_active=True,
    )
    math = Subject(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="Mathematics", code="MAT", default_total_marks=100,
        category="SECONDARY_O", display_order=2, is_active=True,
    )
    db.add_all([gs, eng, math])
    await db.flush()
    test_class.grading_system_id = gs.id

    cs_eng = ClassSubject(
        id=uuid.uuid4(), class_id=test_class.id, subject_id=eng.id,
        total_marks=100, is_compulsory=True, display_order=1,
    )
    cs_math = ClassSubject(
        id=uuid.uuid4(), class_id=test_class.id, subject_id=math.id,
        total_marks=50, is_compulsory=True, display_order=2,
    )
    db.add_all([cs_eng, cs_math])
    await db.flush()

    section_id = "sec-exam-grades"
    template = ReportTemplate(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="Term Report", report_type="REPORT_CARD", frequency="TERMLY",
        sections=[{
            "id": section_id, "type": "ACADEMIC_GRADES",
            "title": "Marks", "display_order": 1,
            "subjects": [
                {"id": str(eng.id), "name": "English", "total_marks": 100},
                {"id": str(math.id), "name": "Mathematics", "total_marks": 50},
            ],
            "grading_system": ZIMSEC_BANDS,
        }],
        grading_system_id=gs.id, is_active=True,
    )
    db.add(template)
    await db.flush()

    def mk_student(fn, ln):
        s = Student(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            first_name=fn, last_name=ln,
            class_id=test_class.id, is_active=True,
        )
        db.add(s)
        return s

    alice = mk_student("Alice", "Ace")
    bob = mk_student("Bob", "Average")
    await db.commit()
    return {
        "gs": gs, "eng": eng, "math": math,
        "template": template, "section_id": section_id,
        "alice": alice, "bob": bob,
    }


class TestCreateExam:
    async def test_auto_seeds_one_paper_per_class_subject(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, exam_seed,
    ):
        svc = get_exam_service()
        exam = await svc.create_exam(
            db, name="End of Term 1", class_id=test_class.id,
            academic_year="2026", term=1,
        )
        await db.commit()
        # Two class subjects → two papers, with each paper's total_marks
        # pulled from the class subject's effective_total_marks.
        assert len(exam.exam_subjects) == 2
        totals = {es.subject_id: es.total_marks for es in exam.exam_subjects}
        assert totals[exam_seed["eng"].id] == 100
        assert totals[exam_seed["math"].id] == 50

    async def test_pins_class_grading_system(
        self, db: AsyncSession, test_class: SchoolClass, exam_seed,
    ):
        svc = get_exam_service()
        exam = await svc.create_exam(
            db, name="T1", class_id=test_class.id,
            academic_year="2026", term=1,
        )
        await db.commit()
        # The class's grading system is captured on the exam so later
        # tenant-default swaps don't mutate historical grades.
        assert exam.grading_system_id == exam_seed["gs"].id

    async def test_duplicate_name_blocks(
        self, db: AsyncSession, test_class: SchoolClass, exam_seed,
    ):
        from app.exceptions import ConflictException
        svc = get_exam_service()
        await svc.create_exam(
            db, name="Midterm", class_id=test_class.id,
            academic_year="2026", term=1,
        )
        await db.commit()
        with pytest.raises(ConflictException):
            await svc.create_exam(
                db, name="Midterm", class_id=test_class.id,
                academic_year="2026", term=1,
            )


class TestMarking:
    async def test_percentage_and_grade_computed(
        self, db: AsyncSession, test_class: SchoolClass, exam_seed,
    ):
        svc = get_exam_service()
        exam = await svc.create_exam(
            db, name="T1", class_id=test_class.id,
            academic_year="2026", term=1,
        )
        await db.commit()
        es_eng = next(es for es in exam.exam_subjects if es.subject_id == exam_seed["eng"].id)

        results = await svc.save_results(db, es_eng.id, rows=[
            {"student_id": str(exam_seed["alice"].id), "marks_obtained": 85},
            {"student_id": str(exam_seed["bob"].id), "marks_obtained": 45},
        ])
        await db.commit()
        by_student = {r.student_id: r for r in results}
        alice = by_student[exam_seed["alice"].id]
        bob = by_student[exam_seed["bob"].id]
        # English is /100 so marks == percentage
        assert alice.percentage == Decimal("85.00")
        assert alice.grade_letter == "A"
        assert bob.percentage == Decimal("45.00")
        assert bob.grade_letter == "D"  # ZIMSEC D is 45-49

    async def test_scales_to_total_marks(
        self, db: AsyncSession, test_class: SchoolClass, exam_seed,
    ):
        """Maths paper is /50 — 25 marks = 50% = C."""
        svc = get_exam_service()
        exam = await svc.create_exam(
            db, name="T1", class_id=test_class.id,
            academic_year="2026", term=1,
        )
        await db.commit()
        es_math = next(es for es in exam.exam_subjects if es.subject_id == exam_seed["math"].id)
        results = await svc.save_results(db, es_math.id, rows=[
            {"student_id": str(exam_seed["alice"].id), "marks_obtained": 25},
        ])
        await db.commit()
        assert results[0].percentage == Decimal("50.00")
        assert results[0].grade_letter == "C"

    async def test_rejects_out_of_range_marks(
        self, db: AsyncSession, test_class: SchoolClass, exam_seed,
    ):
        from app.exceptions import ValidationException
        svc = get_exam_service()
        exam = await svc.create_exam(
            db, name="T1", class_id=test_class.id,
            academic_year="2026", term=1,
        )
        await db.commit()
        es_math = next(es for es in exam.exam_subjects if es.subject_id == exam_seed["math"].id)
        # Math is /50 — 60 should be rejected
        with pytest.raises(ValidationException):
            await svc.save_results(db, es_math.id, rows=[
                {"student_id": str(exam_seed["alice"].id), "marks_obtained": 60},
            ])


class TestFinalize:
    async def test_pushes_marks_into_report_card(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, exam_seed,
    ):
        svc = get_exam_service()
        exam = await svc.create_exam(
            db, name="T1", class_id=test_class.id,
            academic_year="2026", term=1,
            report_template_id=exam_seed["template"].id,
        )
        await db.commit()
        es_eng = next(es for es in exam.exam_subjects if es.subject_id == exam_seed["eng"].id)
        es_math = next(es for es in exam.exam_subjects if es.subject_id == exam_seed["math"].id)
        await svc.save_results(db, es_eng.id, rows=[
            {"student_id": str(exam_seed["alice"].id), "marks_obtained": 85},
            {"student_id": str(exam_seed["bob"].id), "marks_obtained": 55},
        ])
        await svc.save_results(db, es_math.id, rows=[
            {"student_id": str(exam_seed["alice"].id), "marks_obtained": 30},
            {"student_id": str(exam_seed["bob"].id), "marks_obtained": 20},
        ])
        await db.commit()

        summary = await svc.finalize_exam(db, exam.id)
        await db.commit()

        # A report was drafted for each of the two students
        assert summary["reports_created"] == 2
        reports = (await db.execute(
            select(DailyReport).where(
                DailyReport.tenant_id == test_tenant.id,
                DailyReport.template_id == exam_seed["template"].id,
            )
        )).scalars().all()
        assert len(reports) == 2

        # Alice's report carries both subjects under the ACADEMIC_GRADES
        # section with the raw marks we saved
        alice_report = next(r for r in reports if r.student_id == exam_seed["alice"].id)
        section = alice_report.report_data["sections"][exam_seed["section_id"]]
        assert section[str(exam_seed["eng"].id)]["marks_obtained"] == 85.0
        assert section[str(exam_seed["math"].id)]["marks_obtained"] == 30.0

    async def test_finalized_exam_cannot_be_re_marked(
        self, db: AsyncSession, test_class: SchoolClass, exam_seed,
    ):
        from app.exceptions import ValidationException
        svc = get_exam_service()
        exam = await svc.create_exam(
            db, name="T1", class_id=test_class.id,
            academic_year="2026", term=1,
            report_template_id=exam_seed["template"].id,
        )
        await db.commit()
        es_eng = next(es for es in exam.exam_subjects if es.subject_id == exam_seed["eng"].id)
        await svc.save_results(db, es_eng.id, rows=[
            {"student_id": str(exam_seed["alice"].id), "marks_obtained": 80},
        ])
        await svc.finalize_exam(db, exam.id)
        await db.commit()
        with pytest.raises(ValidationException):
            await svc.save_results(db, es_eng.id, rows=[
                {"student_id": str(exam_seed["alice"].id), "marks_obtained": 90},
            ])
