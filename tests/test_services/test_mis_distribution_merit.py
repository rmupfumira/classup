"""Phase 3 — Grade Distribution + Merit Lists tests."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolClass, Student, Tenant, User
from app.models.academic import GradingSystem, Subject
from app.models.grade_level import GradeLevel
from app.models.report import DailyReport, ReportTemplate
from app.services.mis_service import get_mis_service


ZIMSEC_BANDS = [
    {"min": 80, "max": 100, "grade": "A", "description": "Distinction"},
    {"min": 70, "max": 79,  "grade": "B", "description": "Merit"},
    {"min": 60, "max": 69,  "grade": "C", "description": "Credit"},
    {"min": 50, "max": 59,  "grade": "D", "description": "Pass"},
    {"min": 0,  "max": 49,  "grade": "U", "description": "Fail"},
]


@pytest_asyncio.fixture
async def distribution_seed(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, test_admin: User,
):
    gs = GradingSystem(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="ZIMSEC", description="",
        is_default=True, is_active=True, grades=list(ZIMSEC_BANDS),
    )
    db.add(gs)

    subj = Subject(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="Science", code="SCI", default_total_marks=100,
        category="SECONDARY_O", display_order=1, is_active=True,
    )
    db.add(subj)
    await db.flush()

    section_id = "sec-grades"
    template = ReportTemplate(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="Term report", report_type="REPORT_CARD", frequency="TERMLY",
        sections=[{
            "id": section_id, "type": "ACADEMIC_GRADES",
            "title": "Marks", "display_order": 1,
            "subjects": [{"id": str(subj.id), "name": "Science", "total_marks": 100}],
            "grading_system": ZIMSEC_BANDS,
        }],
        grading_system_id=gs.id, is_active=True,
    )
    db.add(template)
    await db.flush()

    today = date.today()
    # Seed 5 students with specific marks so bin counts and rankings
    # are easy to assert on.
    student_marks = [
        ("Alice",   "Ace",    92),  # A
        ("Ben",     "Best",   85),  # A
        ("Cara",    "Clever", 68),  # C
        ("Dan",     "Decent", 55),  # D
        ("Eve",     "Flunk",  30),  # U
    ]
    students: list[Student] = []
    for fn, ln, marks in student_marks:
        s = Student(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            first_name=fn, last_name=ln,
            class_id=test_class.id, is_active=True,
        )
        db.add(s)
        students.append(s)
    await db.flush()
    for s, (_, _, marks) in zip(students, student_marks):
        db.add(DailyReport(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            student_id=s.id, class_id=test_class.id,
            template_id=template.id, report_date=today,
            status="FINALIZED",
            report_data={"sections": {section_id: {
                str(subj.id): {"marks_obtained": marks},
            }}},
            created_by=test_admin.id,
        ))
    await db.commit()

    return {
        "from_date": today - timedelta(days=1),
        "to_date": today + timedelta(days=1),
        "subject_id": subj.id,
        "students": students,
        "marks": [m[2] for m in student_marks],
    }


class TestGradeDistribution:
    async def test_histogram_bins_counts(
        self, db: AsyncSession, distribution_seed,
    ):
        svc = get_mis_service()
        data = await svc.grade_distribution(
            db,
            from_date=distribution_seed["from_date"],
            to_date=distribution_seed["to_date"],
            bin_size=10,
        )
        # 5 entries across 5 bins (30-39, 50-59, 60-69, 80-89, 90-100)
        assert data["n"] == 5
        bins = {b["label"]: b["count"] for b in data["histogram"]}
        assert bins.get("30-39%") == 1  # Eve
        assert bins.get("50-59%") == 1  # Dan
        assert bins.get("60-69%") == 1  # Cara
        assert bins.get("80-89%") == 1  # Ben
        assert bins.get("90-100%") == 1  # Alice

    async def test_mean_and_stddev_match_statistics(
        self, db: AsyncSession, distribution_seed,
    ):
        from statistics import mean, stdev
        svc = get_mis_service()
        data = await svc.grade_distribution(
            db,
            from_date=distribution_seed["from_date"],
            to_date=distribution_seed["to_date"],
        )
        marks = distribution_seed["marks"]
        assert data["mean"] == pytest.approx(mean(marks), abs=0.01)
        assert data["stddev"] == pytest.approx(stdev(marks), abs=0.01)

    async def test_band_histogram_matches_grading(
        self, db: AsyncSession, distribution_seed,
    ):
        svc = get_mis_service()
        data = await svc.grade_distribution(
            db,
            from_date=distribution_seed["from_date"],
            to_date=distribution_seed["to_date"],
        )
        by_grade = {b["grade"]: b["count"] for b in data["band_histogram"]}
        assert by_grade["A"] == 2   # Alice + Ben
        assert by_grade["C"] == 1   # Cara (68%)
        assert by_grade["D"] == 1   # Dan (55%)
        assert by_grade["U"] == 1   # Eve

    async def test_subject_filter_narrows_scope(
        self, db: AsyncSession, distribution_seed,
    ):
        svc = get_mis_service()
        # With the subject filter, n should be the same 5 (only subject)
        data = await svc.grade_distribution(
            db,
            from_date=distribution_seed["from_date"],
            to_date=distribution_seed["to_date"],
            subject_id=distribution_seed["subject_id"],
        )
        assert data["n"] == 5

    async def test_pass_mark_pulled_from_grading(
        self, db: AsyncSession, distribution_seed,
    ):
        svc = get_mis_service()
        data = await svc.grade_distribution(
            db,
            from_date=distribution_seed["from_date"],
            to_date=distribution_seed["to_date"],
        )
        # ZIMSEC bands: second-lowest band min is 50 (Pass). That's what
        # kpi_summary + distribution report as the pass mark.
        assert data["pass_mark_pct"] == 50.0

    async def test_empty_window_returns_zero_values(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        svc = get_mis_service()
        data = await svc.grade_distribution(
            db, from_date=date(2000, 1, 1), to_date=date(2000, 1, 31),
        )
        assert data["n"] == 0
        assert data["mean"] == 0.0
        # Every bin is 0
        assert all(b["count"] == 0 for b in data["histogram"])


class TestMeritList:
    async def test_school_ranking_descending_by_aggregate(
        self, db: AsyncSession, distribution_seed,
    ):
        svc = get_mis_service()
        data = await svc.merit_list(
            db,
            from_date=distribution_seed["from_date"],
            to_date=distribution_seed["to_date"],
            scope="SCHOOL",
        )
        rows = data["ranking"]
        assert len(rows) == 5
        # Rank 1 is Alice (92%), rank 2 is Ben (85%)
        assert rows[0]["student_name"] == "Alice Ace"
        assert rows[0]["rank"] == 1
        assert rows[1]["student_name"] == "Ben Best"
        assert rows[1]["rank"] == 2

    async def test_top_n_limits_results(
        self, db: AsyncSession, distribution_seed,
    ):
        svc = get_mis_service()
        data = await svc.merit_list(
            db,
            from_date=distribution_seed["from_date"],
            to_date=distribution_seed["to_date"],
            scope="SCHOOL",
            top_n=3,
        )
        assert len(data["ranking"]) == 3
        # total_in_scope is the pool size, not the cut list
        assert data["total_in_scope"] == 5

    async def test_class_scope_only_counts_that_class(
        self, db: AsyncSession, distribution_seed, test_class: SchoolClass,
    ):
        svc = get_mis_service()
        data = await svc.merit_list(
            db,
            from_date=distribution_seed["from_date"],
            to_date=distribution_seed["to_date"],
            scope="CLASS",
            class_id=test_class.id,
        )
        # All 5 seeded students are in test_class so all 5 are ranked
        assert len(data["ranking"]) == 5

    async def test_tie_handling_shares_rank(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass,
        test_admin: User,
    ):
        """Two identical scores → both share rank 1, third distinct score
        gets rank 3 (competition style)."""
        gs = GradingSystem(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            name="Generic", description="",
            is_default=True, is_active=True, grades=list(ZIMSEC_BANDS),
        )
        subj = Subject(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            name="Only", code="ONE", default_total_marks=100,
            category="ALL", display_order=1, is_active=True,
        )
        db.add_all([gs, subj])
        await db.flush()

        section_id = "sec"
        template = ReportTemplate(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            name="t", report_type="REPORT_CARD", frequency="TERMLY",
            sections=[{
                "id": section_id, "type": "ACADEMIC_GRADES",
                "title": "", "display_order": 1,
                "subjects": [{"id": str(subj.id), "name": "Only", "total_marks": 100}],
                "grading_system": ZIMSEC_BANDS,
            }],
            grading_system_id=gs.id, is_active=True,
        )
        db.add(template)
        await db.flush()

        today = date.today()
        for i, (fn, marks) in enumerate([("Twin1", 80), ("Twin2", 80), ("Third", 70)]):
            s = Student(
                id=uuid.uuid4(), tenant_id=test_tenant.id,
                first_name=fn, last_name="Test",
                class_id=test_class.id, is_active=True,
            )
            db.add(s)
            await db.flush()
            db.add(DailyReport(
                id=uuid.uuid4(), tenant_id=test_tenant.id,
                student_id=s.id, class_id=test_class.id,
                template_id=template.id, report_date=today,
                status="FINALIZED",
                report_data={"sections": {section_id: {
                    str(subj.id): {"marks_obtained": marks},
                }}},
                created_by=test_admin.id,
            ))
        await db.commit()

        svc = get_mis_service()
        data = await svc.merit_list(
            db,
            from_date=today - timedelta(days=1),
            to_date=today + timedelta(days=1),
            scope="SCHOOL",
        )
        ranks = [r["rank"] for r in data["ranking"]]
        # 1, 1, 3 — competition style
        assert ranks == [1, 1, 3]
