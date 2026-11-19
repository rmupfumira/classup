"""Phase 5 — Early Warning rules + scanner tests."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AttendanceRecord,
    SchoolClass,
    Student,
    Tenant,
    User,
)
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


# --------------------------------------------------------------------- #
# Rules — default + overrides round-trip
# --------------------------------------------------------------------- #


class TestRules:
    async def test_defaults_when_no_overrides(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        svc = get_mis_service()
        rules = await svc.get_early_warning_rules(db)
        assert rules == svc.DEFAULT_RULES

    async def test_overrides_persist_and_merge(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        svc = get_mis_service()
        # Override just two keys — the rest stay as defaults
        await svc.save_early_warning_rules(
            db, {"aggregate_avg_high": 55.0, "attendance_high": 85.0},
        )
        await db.commit()

        rules = await svc.get_early_warning_rules(db)
        assert rules["aggregate_avg_high"] == 55.0
        assert rules["attendance_high"] == 85.0
        # Non-overridden keys keep the default
        assert rules["aggregate_avg_critical"] == svc.DEFAULT_RULES["aggregate_avg_critical"]
        assert rules["decline_high"] == svc.DEFAULT_RULES["decline_high"]

    async def test_junk_values_ignored(
        self, db: AsyncSession, test_tenant: Tenant,
    ):
        """A non-numeric value should be ignored, not crash the scan."""
        svc = get_mis_service()
        await svc.save_early_warning_rules(
            db, {"attendance_medium": "not a number"},  # type: ignore[arg-type]
        )
        await db.commit()
        rules = await svc.get_early_warning_rules(db)
        assert rules["attendance_medium"] == svc.DEFAULT_RULES["attendance_medium"]


# --------------------------------------------------------------------- #
# Scanner with real data
# --------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def at_risk_seed(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, test_admin: User,
):
    """Seed four students with marks engineered to trip different rules:
      - Alice:  aggregate 25% → aggregate CRITICAL
      - Ben:    aggregate 55% + attendance 65% → aggregate MEDIUM, attendance HIGH
      - Carol:  aggregate 80% but term-over-term drop of 20% → decline CRITICAL
      - Dan:    aggregate 90% + attendance 95% → nothing flagged
    """
    gs = GradingSystem(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="ZIMSEC", description="",
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
    tpl = ReportTemplate(
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
    db.add(tpl)
    await db.flush()

    year = date.today().year

    def _q(q: int) -> date:
        month = {1: 2, 2: 5, 3: 8, 4: 11}[q]
        return date(year, month, 15)

    def mk_student(fn, ln):
        s = Student(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            first_name=fn, last_name=ln,
            class_id=test_class.id, is_active=True,
        )
        db.add(s)
        return s

    alice = mk_student("Alice", "Low")        # single term, 25%
    ben = mk_student("Ben", "Mid")             # single term, 55%
    carol = mk_student("Carol", "Dropped")     # two terms, decline 20%
    dan = mk_student("Dan", "Safe")            # single term, 90%
    await db.flush()

    def mk_report(student, q, score):
        db.add(DailyReport(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            student_id=student.id, class_id=test_class.id,
            template_id=tpl.id, report_date=_q(q),
            status="FINALIZED",
            report_data={"sections": {section_id: {
                str(subj.id): {"marks_obtained": score},
            }}},
            created_by=test_admin.id,
        ))

    mk_report(alice, 3, 25)
    mk_report(ben, 3, 55)
    mk_report(carol, 2, 100)
    mk_report(carol, 3, 80)
    mk_report(dan, 3, 90)

    # Attendance — only Ben is low (65% < 70% HIGH threshold)
    def mk_att(student, status, day):
        db.add(AttendanceRecord(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            student_id=student.id, class_id=test_class.id,
            date=date(year, 8, day),
            status=status, recorded_by=test_admin.id,
        ))

    # Ben: 13 PRESENT + 7 ABSENT = 65%
    for i in range(1, 14):
        mk_att(ben, "PRESENT", i)
    for i in range(14, 21):
        mk_att(ben, "ABSENT", i)
    # Dan: all PRESENT
    for i in range(1, 21):
        mk_att(dan, "PRESENT", i)

    await db.commit()

    return {
        "alice": alice, "ben": ben, "carol": carol, "dan": dan,
        "from_date": date(year, 1, 1),
        "to_date": date(year, 12, 31),
    }


class TestScanner:
    async def test_flags_low_aggregate_as_critical(
        self, db: AsyncSession, at_risk_seed,
    ):
        svc = get_mis_service()
        data = await svc.scan_at_risk_students(
            db,
            from_date=at_risk_seed["from_date"],
            to_date=at_risk_seed["to_date"],
        )
        alice = next(
            (s for s in data["students"] if s["student_name"] == "Alice Low"),
            None,
        )
        assert alice is not None
        assert alice["level"] == "CRITICAL"
        kinds = {t["kind"] for t in alice["triggered_rules"]}
        assert "aggregate" in kinds

    async def test_attendance_trip(
        self, db: AsyncSession, at_risk_seed,
    ):
        svc = get_mis_service()
        data = await svc.scan_at_risk_students(
            db,
            from_date=at_risk_seed["from_date"],
            to_date=at_risk_seed["to_date"],
        )
        ben = next(
            (s for s in data["students"] if s["student_name"] == "Ben Mid"),
            None,
        )
        assert ben is not None
        kinds = {t["kind"] for t in ben["triggered_rules"]}
        assert "attendance" in kinds
        # Ben's attendance is 65% → HIGH (below 70 HIGH threshold).
        attendance_trip = next(t for t in ben["triggered_rules"] if t["kind"] == "attendance")
        assert attendance_trip["level"] == "HIGH"

    async def test_decline_trip(
        self, db: AsyncSession, at_risk_seed,
    ):
        svc = get_mis_service()
        data = await svc.scan_at_risk_students(
            db,
            from_date=at_risk_seed["from_date"],
            to_date=at_risk_seed["to_date"],
        )
        carol = next(
            (s for s in data["students"] if s["student_name"] == "Carol Dropped"),
            None,
        )
        assert carol is not None
        kinds = {t["kind"] for t in carol["triggered_rules"]}
        assert "decline" in kinds
        # 100 → 80 = 20 drop → CRITICAL (≥15 default)
        decline_trip = next(t for t in carol["triggered_rules"] if t["kind"] == "decline")
        assert decline_trip["level"] == "CRITICAL"
        assert decline_trip["value"] == pytest.approx(20.0)

    async def test_safe_student_not_in_list(
        self, db: AsyncSession, at_risk_seed,
    ):
        svc = get_mis_service()
        data = await svc.scan_at_risk_students(
            db,
            from_date=at_risk_seed["from_date"],
            to_date=at_risk_seed["to_date"],
        )
        names = {s["student_name"] for s in data["students"]}
        assert "Dan Safe" not in names

    async def test_level_counts(
        self, db: AsyncSession, at_risk_seed,
    ):
        svc = get_mis_service()
        data = await svc.scan_at_risk_students(
            db,
            from_date=at_risk_seed["from_date"],
            to_date=at_risk_seed["to_date"],
        )
        # 3 flagged — Alice (CRITICAL), Ben (HIGH or higher), Carol (CRITICAL)
        assert data["total_at_risk"] == 3
        # Sorted worst-first
        assert data["students"][0]["level"] in {"CRITICAL"}

    async def test_override_narrows_scope(
        self, db: AsyncSession, at_risk_seed,
    ):
        """Loosening the aggregate thresholds should drop Ben + Alice
        (who were trip based on aggregate) from the list, while Carol
        stays on via decline."""
        svc = get_mis_service()
        await svc.save_early_warning_rules(db, {
            "aggregate_avg_critical": 0.0,
            "aggregate_avg_high": 0.0,
            "aggregate_avg_medium": 0.0,
            "attendance_critical": 0.0,
            "attendance_high": 0.0,
            "attendance_medium": 0.0,
            # decline thresholds unchanged
        })
        await db.commit()
        data = await svc.scan_at_risk_students(
            db,
            from_date=at_risk_seed["from_date"],
            to_date=at_risk_seed["to_date"],
        )
        names = {s["student_name"] for s in data["students"]}
        assert names == {"Carol Dropped"}
