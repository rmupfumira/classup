"""MIS Executive Dashboard tests — Phase 1.

Pins the KPI math so Phase 2+ can build on it without regressing.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttendanceRecord, SchoolClass, Student, Tenant, User
from app.services.mis_service import get_mis_service


@pytest_asyncio.fixture
async def attendance_seed(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, test_admin: User,
):
    """Three students: one perfect, one chronic absentee, one middling."""
    today = date.today()
    base = today - timedelta(days=10)

    def mk_student(fn, ln):
        s = Student(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            first_name=fn, last_name=ln,
            class_id=test_class.id, is_active=True,
        )
        db.add(s)
        return s

    perfect = mk_student("Alice", "Perfect")
    chronic = mk_student("Bob", "Absent")
    middling = mk_student("Carol", "Average")
    await db.flush()

    def mk(day_offset, student_id, status):
        db.add(AttendanceRecord(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            student_id=student_id, class_id=test_class.id,
            date=base + timedelta(days=day_offset),
            status=status, recorded_by=test_admin.id,
        ))

    # 10 sessions
    for i in range(10):
        mk(i, perfect.id, "PRESENT")      # 100%
    for i in range(10):
        # Chronic: 4 PRESENT + 6 ABSENT = 40%
        mk(i, chronic.id, "PRESENT" if i < 4 else "ABSENT")
    for i in range(10):
        # Middling: 9 PRESENT + 1 ABSENT = 90%
        mk(i, middling.id, "PRESENT" if i < 9 else "ABSENT")

    await db.commit()
    return {
        "from_date": base, "to_date": today,
        "perfect": perfect, "chronic": chronic, "middling": middling,
    }


class TestAttendanceStats:
    async def test_whole_school_rate(
        self, db: AsyncSession, attendance_seed,
    ):
        svc = get_mis_service()
        stats = await svc.attendance_stats(
            db,
            from_date=attendance_seed["from_date"],
            to_date=attendance_seed["to_date"],
        )
        # 10 perfect + 4 chronic + 9 middling = 23 attended out of 30
        assert stats["total"] == 30
        assert stats["present"] == 23
        assert stats["absent"] == 7
        # 23/30 = 76.67
        assert stats["rate_pct"] == pytest.approx(76.67, abs=0.1)


class TestChronicAbsenteeism:
    async def test_flags_only_below_threshold(
        self, db: AsyncSession, attendance_seed,
    ):
        svc = get_mis_service()
        rows = await svc.chronic_absenteeism(
            db,
            from_date=attendance_seed["from_date"],
            to_date=attendance_seed["to_date"],
            threshold_pct=80.0,
        )
        # Only Bob (40%) is below the 80% threshold.
        assert len(rows) == 1
        assert rows[0]["student_name"] == "Bob Absent"
        assert rows[0]["attendance_rate_pct"] == pytest.approx(40.0)

    async def test_threshold_tightens(
        self, db: AsyncSession, attendance_seed,
    ):
        svc = get_mis_service()
        # At 95% threshold, Carol (90%) joins Bob (40%)
        rows = await svc.chronic_absenteeism(
            db,
            from_date=attendance_seed["from_date"],
            to_date=attendance_seed["to_date"],
            threshold_pct=95.0,
        )
        names = {r["student_name"] for r in rows}
        assert "Bob Absent" in names
        assert "Carol Average" in names
        assert "Alice Perfect" not in names  # 100% stays clear

    async def test_sorted_lowest_rate_first(
        self, db: AsyncSession, attendance_seed,
    ):
        svc = get_mis_service()
        rows = await svc.chronic_absenteeism(
            db,
            from_date=attendance_seed["from_date"],
            to_date=attendance_seed["to_date"],
            threshold_pct=100.0,
        )
        # Lowest rate first
        rates = [r["attendance_rate_pct"] for r in rows]
        assert rates == sorted(rates)


class TestKpiSummary:
    async def test_combines_attendance_and_at_risk(
        self, db: AsyncSession, attendance_seed,
    ):
        """KPI summary pulls attendance + at-risk together. Even with no
        academic data, the attendance side still rolls up cleanly."""
        svc = get_mis_service()
        result = await svc.kpi_summary(
            db,
            from_date=attendance_seed["from_date"],
            to_date=attendance_seed["to_date"],
        )
        assert result["attendance"]["total"] == 30
        # Bob is chronic (40% < 80%); 1 at-risk student
        assert result["at_risk"]["chronic_absentees_count"] == 1
        assert result["at_risk"]["count"] >= 1

    async def test_no_academic_data_is_not_an_error(
        self, db: AsyncSession, attendance_seed,
    ):
        """When no finalised reports exist in the window, academic
        KPIs are zeroed-out rather than crashing."""
        svc = get_mis_service()
        result = await svc.kpi_summary(
            db,
            from_date=attendance_seed["from_date"],
            to_date=attendance_seed["to_date"],
        )
        assert result["academic"]["students_with_marks"] == 0
        assert result["academic"]["overall_average"] == 0.0
        assert result["academic"]["top_class"] is None
        assert result["academic"]["top_subject"] is None
