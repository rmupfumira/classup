"""Phase 4 — Longitudinal performance tests.

Seed a student across three calendar quarters of the same year with
varying marks + a classmate so rank computations have something to
contend against. Verify that per-term aggregates, rank movement,
delta percentages, strengths and weaknesses all come out the right
way round.
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
from app.services.mis_service import get_mis_service


ZIMSEC_BANDS = [
    {"min": 80, "max": 100, "grade": "A", "description": "Distinction"},
    {"min": 70, "max": 79,  "grade": "B", "description": "Merit"},
    {"min": 60, "max": 69,  "grade": "C", "description": "Credit"},
    {"min": 50, "max": 59,  "grade": "D", "description": "Pass"},
    {"min": 0,  "max": 49,  "grade": "U", "description": "Fail"},
]


def _q_middle(year: int, quarter: int) -> date:
    """A date that lands squarely inside the given calendar quarter so
    _term_key_for_date rolls it into (year, quarter)."""
    # Use month 2 (Feb), 5 (May), 8 (Aug), 11 (Nov) to be safely mid-quarter
    month = {1: 2, 2: 5, 3: 8, 4: 11}[quarter]
    return date(year, month, 15)


@pytest_asyncio.fixture
async def longitudinal_seed(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, test_admin: User,
):
    """Two students, three terms of marks each:
      Alice:  Term 1 = 60, Term 2 = 70, Term 3 = 80  (climbing)
      Bob:    Term 1 = 75, Term 2 = 70, Term 3 = 65  (falling)
    Alice overtakes Bob in Term 3. One subject (English) so aggregates
    match raw marks.
    """
    gs = GradingSystem(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="ZIMSEC", description="",
        is_default=True, is_active=True, grades=list(ZIMSEC_BANDS),
    )
    eng = Subject(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="English", code="ENG", default_total_marks=100,
        category="SECONDARY_O", display_order=1, is_active=True,
    )
    db.add_all([gs, eng])
    await db.flush()

    section_id = "sec-grades"
    template = ReportTemplate(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="Term report", report_type="REPORT_CARD", frequency="TERMLY",
        sections=[{
            "id": section_id, "type": "ACADEMIC_GRADES",
            "title": "Marks", "display_order": 1,
            "subjects": [{"id": str(eng.id), "name": "English", "total_marks": 100}],
            "grading_system": ZIMSEC_BANDS,
        }],
        grading_system_id=gs.id, is_active=True,
    )
    db.add(template)
    await db.flush()

    alice = Student(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        first_name="Alice", last_name="Climbing",
        class_id=test_class.id, is_active=True,
    )
    bob = Student(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        first_name="Bob", last_name="Falling",
        class_id=test_class.id, is_active=True,
    )
    db.add_all([alice, bob])
    await db.flush()

    year = date.today().year
    marks = [
        (alice, 1, 60),
        (alice, 2, 70),
        (alice, 3, 80),
        (bob, 1, 75),
        (bob, 2, 70),
        (bob, 3, 65),
    ]
    for student, q, score in marks:
        db.add(DailyReport(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            student_id=student.id, class_id=test_class.id,
            template_id=template.id,
            report_date=_q_middle(year, q),
            status="FINALIZED",
            report_data={"sections": {section_id: {
                str(eng.id): {"marks_obtained": score},
            }}},
            created_by=test_admin.id,
        ))
    await db.commit()

    return {
        "alice": alice, "bob": bob,
        "year": year,
        "from_date": date(year, 1, 1),
        "to_date": date(year, 12, 31),
    }


class TestLongitudinal:
    async def test_terms_sorted_oldest_first(
        self, db: AsyncSession, longitudinal_seed,
    ):
        svc = get_mis_service()
        data = await svc.student_longitudinal(
            db,
            student_id=longitudinal_seed["alice"].id,
            from_date=longitudinal_seed["from_date"],
            to_date=longitudinal_seed["to_date"],
        )
        terms = data["terms"]
        # 3 terms for Alice
        assert len(terms) == 3
        # Sorted ascending
        assert [t["term"] for t in terms] == [1, 2, 3]
        # Aggregate numbers match seeded marks (one subject)
        assert terms[0]["aggregate_pct"] == pytest.approx(60.0)
        assert terms[1]["aggregate_pct"] == pytest.approx(70.0)
        assert terms[2]["aggregate_pct"] == pytest.approx(80.0)

    async def test_aggregate_delta_shown(
        self, db: AsyncSession, longitudinal_seed,
    ):
        svc = get_mis_service()
        data = await svc.student_longitudinal(
            db,
            student_id=longitudinal_seed["alice"].id,
            from_date=longitudinal_seed["from_date"],
            to_date=longitudinal_seed["to_date"],
        )
        terms = data["terms"]
        # First term has no previous → delta None
        assert terms[0]["aggregate_delta_pct"] is None
        # Term 2: 70 - 60 = +10
        assert terms[1]["aggregate_delta_pct"] == pytest.approx(10.0)
        # Term 3: 80 - 70 = +10
        assert terms[2]["aggregate_delta_pct"] == pytest.approx(10.0)

    async def test_rank_movement_when_overtaking(
        self, db: AsyncSession, longitudinal_seed,
    ):
        """Alice is #2 in Terms 1-2 (behind Bob), then overtakes him
        in Term 3 (she climbed one position)."""
        svc = get_mis_service()
        data = await svc.student_longitudinal(
            db,
            student_id=longitudinal_seed["alice"].id,
            from_date=longitudinal_seed["from_date"],
            to_date=longitudinal_seed["to_date"],
        )
        terms = data["terms"]
        # Term 1: Bob (75) > Alice (60) → Alice is #2
        assert terms[0]["rank_in_class"] == 2
        assert terms[0]["class_size"] == 2
        # Term 2: Bob (70) = Alice (70) — tied for #1
        # Competition-style tie handling: both at #1
        assert terms[1]["rank_in_class"] == 1
        # Term 3: Alice (80) > Bob (65) → Alice is #1
        assert terms[2]["rank_in_class"] == 1
        # Rank movement Term 1 → Term 2: +1 (2→1)
        assert terms[1]["rank_delta"] == 1

    async def test_strengths_and_weaknesses(
        self, db: AsyncSession, longitudinal_seed,
    ):
        svc = get_mis_service()
        data = await svc.student_longitudinal(
            db,
            student_id=longitudinal_seed["alice"].id,
            from_date=longitudinal_seed["from_date"],
            to_date=longitudinal_seed["to_date"],
        )
        # Only one subject (English) → appears in both
        assert data["strengths"][0]["subject_name"] == "English"
        assert data["weaknesses"][0]["subject_name"] == "English"


class TestStudentProgress:
    async def test_current_and_previous_populated(
        self, db: AsyncSession, longitudinal_seed,
    ):
        svc = get_mis_service()
        data = await svc.student_progress(
            db,
            student_id=longitudinal_seed["alice"].id,
            from_date=longitudinal_seed["from_date"],
            to_date=longitudinal_seed["to_date"],
        )
        assert data["current"]["term"] == 3
        assert data["previous"]["term"] == 2
        assert data["aggregate_delta_pct"] == pytest.approx(10.0)

    async def test_falling_student_shows_negative_delta(
        self, db: AsyncSession, longitudinal_seed,
    ):
        svc = get_mis_service()
        data = await svc.student_progress(
            db,
            student_id=longitudinal_seed["bob"].id,
            from_date=longitudinal_seed["from_date"],
            to_date=longitudinal_seed["to_date"],
        )
        # Bob: Term 2 70 → Term 3 65 = -5
        assert data["aggregate_delta_pct"] == pytest.approx(-5.0)

    async def test_no_reports_for_student_returns_empty(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """A student with no finalised reports returns an empty-shape
        payload rather than a crash."""
        svc = get_mis_service()
        data = await svc.student_longitudinal(
            db,
            student_id=uuid.uuid4(),
            from_date=date(2000, 1, 1),
            to_date=date(2000, 12, 31),
        )
        assert data["student_name"] is None
        assert data["terms"] == []
