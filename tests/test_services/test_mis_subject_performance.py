"""Phase 2 — Subject Performance Analysis tests.

Pins the classification rules + rate math so Phase 3 (distribution
charts) and Phase 5 (early warning) can rely on the same shapes.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolClass, Student, Tenant, User
from app.models.academic import GradingSystem, Subject
from app.models.report import DailyReport, ReportTemplate
from app.services.mis_service import (
    BUCKET_DISTINCTION,
    BUCKET_FAIL,
    BUCKET_MERIT,
    BUCKET_PASS,
    classify_percentage,
    get_mis_service,
)


ZIMSEC_BANDS = [
    {"min": 80, "max": 100, "grade": "A", "description": "Distinction"},
    {"min": 70, "max": 79,  "grade": "B", "description": "Merit"},
    {"min": 60, "max": 69,  "grade": "C", "description": "Credit"},
    {"min": 50, "max": 59,  "grade": "D", "description": "Pass"},
    {"min": 0,  "max": 49,  "grade": "U", "description": "Fail"},
]


# --------------------------------------------------------------------- #
# Pure classify_percentage — no DB needed
# --------------------------------------------------------------------- #


class TestClassifyPercentage:
    def test_top_band_is_distinction(self):
        got = classify_percentage(95, ZIMSEC_BANDS)
        assert got["grade"] == "A"
        assert got["bucket"] == BUCKET_DISTINCTION

    def test_second_band_is_merit(self):
        got = classify_percentage(72, ZIMSEC_BANDS)
        assert got["grade"] == "B"
        assert got["bucket"] == BUCKET_MERIT

    def test_bottom_band_is_fail(self):
        got = classify_percentage(30, ZIMSEC_BANDS)
        assert got["grade"] == "U"
        assert got["bucket"] == BUCKET_FAIL

    def test_below_lowest_band_still_classifies(self):
        # -5% is nonsense but shouldn't crash — falls to the last band
        got = classify_percentage(-5, ZIMSEC_BANDS)
        assert got["bucket"] == BUCKET_FAIL

    def test_no_bands_falls_back_to_50_pct_pass(self):
        assert classify_percentage(60, None)["bucket"] == BUCKET_PASS
        assert classify_percentage(30, None)["bucket"] == BUCKET_FAIL


# --------------------------------------------------------------------- #
# End-to-end subject_performance with seeded reports
# --------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def subject_report_seed(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, test_admin: User,
):
    """Seed:
    - a ZIMSEC grading system (default)
    - two subjects (English + Math)
    - a REPORT_CARD template with an ACADEMIC_GRADES section pointing
      at both subjects
    - four students with varied marks so each bucket is covered
    """
    gs = GradingSystem(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="ZIMSEC O-Level", description="Zimbabwe O-Level",
        is_default=True, is_active=True, grades=list(ZIMSEC_BANDS),
    )
    db.add(gs)

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
    db.add_all([eng, math])
    await db.flush()

    section_id = "sec-grades"
    template = ReportTemplate(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="O-Level Term Report", report_type="REPORT_CARD",
        frequency="TERMLY",
        sections=[{
            "id": section_id,
            "type": "ACADEMIC_GRADES",
            "title": "Academic grades",
            "display_order": 1,
            "subjects": [
                {"id": str(eng.id), "name": "English", "total_marks": 100},
                {"id": str(math.id), "name": "Mathematics", "total_marks": 100},
            ],
            "grading_system": ZIMSEC_BANDS,
        }],
        grading_system_id=gs.id,
        is_active=True,
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

    alice = mk_student("Alice", "Ace")        # Distinction both subjects
    ben = mk_student("Ben", "Merit")           # Merit English, Credit math
    cara = mk_student("Cara", "Pass")          # Pass English, Fail math
    dan = mk_student("Dan", "Fail")            # Fail both
    await db.flush()

    today = date.today()

    def mk_report(student, eng_marks, math_marks):
        db.add(DailyReport(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            student_id=student.id, class_id=test_class.id,
            template_id=template.id, report_date=today,
            status="FINALIZED",
            report_data={"sections": {section_id: {
                str(eng.id): {"marks_obtained": eng_marks},
                str(math.id): {"marks_obtained": math_marks},
            }}},
            created_by=test_admin.id,
        ))

    mk_report(alice, 92, 88)   # Distinction + Distinction
    mk_report(ben, 75, 65)     # Merit + Credit
    mk_report(cara, 55, 40)    # Pass + Fail
    mk_report(dan, 30, 25)     # Fail + Fail
    await db.commit()
    return {
        "from_date": today - timedelta(days=1),
        "to_date": today + timedelta(days=1),
        "eng_id": str(eng.id),
        "math_id": str(math.id),
    }


class TestSubjectPerformance:
    async def test_shape_includes_grading_and_subjects(
        self, db: AsyncSession, subject_report_seed,
    ):
        svc = get_mis_service()
        data = await svc.subject_performance(
            db,
            from_date=subject_report_seed["from_date"],
            to_date=subject_report_seed["to_date"],
        )
        assert data["grading_system"] is not None
        assert data["pass_mark_pct"] == 50.0
        assert len(data["subjects"]) == 2
        # Sorted pass-rate desc — English (75% pass) comes before Math (50%)
        assert data["subjects"][0]["subject_name"] == "English"
        assert data["subjects"][1]["subject_name"] == "Mathematics"

    async def test_english_rates_and_mean(
        self, db: AsyncSession, subject_report_seed,
    ):
        svc = get_mis_service()
        data = await svc.subject_performance(
            db,
            from_date=subject_report_seed["from_date"],
            to_date=subject_report_seed["to_date"],
        )
        eng = next(s for s in data["subjects"] if s["subject_name"] == "English")
        # Scores: 92, 75, 55, 30 → mean = 63, median = 65
        assert eng["student_count"] == 4
        assert eng["mean"] == pytest.approx(63.0)
        assert eng["median"] == pytest.approx(65.0)
        # 3 of 4 pass (anything not in the lowest band). Dan (30) fails.
        assert eng["rates"]["pass_pct"] == pytest.approx(75.0)
        assert eng["rates"]["fail_pct"] == pytest.approx(25.0)
        # 1 distinction (Alice at 92)
        assert eng["rates"]["distinction_pct"] == pytest.approx(25.0)

    async def test_maths_rates(
        self, db: AsyncSession, subject_report_seed,
    ):
        svc = get_mis_service()
        data = await svc.subject_performance(
            db,
            from_date=subject_report_seed["from_date"],
            to_date=subject_report_seed["to_date"],
        )
        math = next(s for s in data["subjects"] if s["subject_name"] == "Mathematics")
        # Scores: 88, 65, 40, 25 → 2 pass (88, 65), 2 fail (40, 25)
        assert math["rates"]["pass_pct"] == pytest.approx(50.0)
        assert math["rates"]["fail_pct"] == pytest.approx(50.0)

    async def test_best_and_worst_students_named(
        self, db: AsyncSession, subject_report_seed,
    ):
        svc = get_mis_service()
        data = await svc.subject_performance(
            db,
            from_date=subject_report_seed["from_date"],
            to_date=subject_report_seed["to_date"],
        )
        eng = next(s for s in data["subjects"] if s["subject_name"] == "English")
        assert eng["best_student"]["name"] == "Alice Ace"
        assert eng["best_student"]["percentage"] == pytest.approx(92.0)
        assert eng["worst_student"]["name"] == "Dan Fail"
        assert eng["worst_student"]["percentage"] == pytest.approx(30.0)

    async def test_band_counts_match_grading(
        self, db: AsyncSession, subject_report_seed,
    ):
        svc = get_mis_service()
        data = await svc.subject_performance(
            db,
            from_date=subject_report_seed["from_date"],
            to_date=subject_report_seed["to_date"],
        )
        eng = next(s for s in data["subjects"] if s["subject_name"] == "English")
        # Alice 92 → A, Ben 75 → B, Cara 55 → D, Dan 30 → U
        assert eng["band_counts"].get("A") == 1
        assert eng["band_counts"].get("B") == 1
        assert eng["band_counts"].get("D") == 1
        assert eng["band_counts"].get("U") == 1

    async def test_empty_window_returns_no_subjects(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """No reports in window → empty subjects list, grading still
        resolved so the UI has something to show."""
        svc = get_mis_service()
        data = await svc.subject_performance(
            db,
            from_date=date(2000, 1, 1),
            to_date=date(2000, 1, 31),
        )
        assert data["subjects"] == []
