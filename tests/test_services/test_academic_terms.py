"""Academic terms + term-based attendance summary.

Owner directive 2026-10-06: report cards must auto-populate term,
academic year, and attendance totals so teachers don't re-count.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttendanceRecord, Student, Tenant
from app.services import academic_terms as terms


@pytest.mark.asyncio
async def test_upsert_and_list_terms(db: AsyncSession, test_tenant: Tenant):
    t = terms.AcademicTerm(
        id="t1-2026", number=1, year=2026, label="Term 1 2026",
        start=date(2026, 1, 15), end=date(2026, 4, 2),
    )
    await terms.upsert_term(db, test_tenant.id, t)
    rows = await terms.list_terms(db, test_tenant.id)
    assert [r.id for r in rows] == ["t1-2026"]
    assert rows[0].label == "Term 1 2026"


@pytest.mark.asyncio
async def test_upsert_replaces_matching_id(
    db: AsyncSession, test_tenant: Tenant,
):
    t1 = terms.AcademicTerm(
        id="t1-2026", number=1, year=2026, label="Term 1 2026",
        start=date(2026, 1, 15), end=date(2026, 4, 2),
    )
    await terms.upsert_term(db, test_tenant.id, t1)
    t2 = terms.AcademicTerm(
        id="t1-2026", number=1, year=2026, label="Term 1 2026 (updated)",
        start=date(2026, 1, 20), end=date(2026, 4, 10),
    )
    await terms.upsert_term(db, test_tenant.id, t2)
    rows = await terms.list_terms(db, test_tenant.id)
    assert len(rows) == 1
    assert rows[0].label == "Term 1 2026 (updated)"
    assert rows[0].start == date(2026, 1, 20)


@pytest.mark.asyncio
async def test_upsert_rejects_bad_date_range(
    db: AsyncSession, test_tenant: Tenant,
):
    t = terms.AcademicTerm(
        id="bad", number=1, year=2026, label="bad",
        start=date(2026, 4, 1), end=date(2026, 1, 1),
    )
    with pytest.raises(ValueError):
        await terms.upsert_term(db, test_tenant.id, t)


@pytest.mark.asyncio
async def test_delete_term(db: AsyncSession, test_tenant: Tenant):
    t = terms.AcademicTerm(
        id="t1-2026", number=1, year=2026, label="Term 1 2026",
        start=date(2026, 1, 15), end=date(2026, 4, 2),
    )
    await terms.upsert_term(db, test_tenant.id, t)
    removed = await terms.delete_term(db, test_tenant.id, "t1-2026")
    assert removed is True
    assert await terms.list_terms(db, test_tenant.id) == []


@pytest.mark.asyncio
async def test_attendance_summary_counts_each_status(
    db: AsyncSession, test_tenant: Tenant, test_class, test_admin
):
    student = Student(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Attendance",
        last_name="Test",
        class_id=test_class.id,
        enrollment_date=date(2026, 1, 1),
        is_active=True,
    )
    db.add(student)
    await db.commit()

    # Spread 5 attendance rows across different statuses on
    # unique dates. The UNIQUE(student_id, date) constraint forces
    # one row per date.
    rows = [
        ("PRESENT", date(2026, 2, 2)),
        ("PRESENT", date(2026, 2, 3)),
        ("ABSENT", date(2026, 2, 4)),
        ("LATE", date(2026, 2, 5)),
        ("EXCUSED", date(2026, 2, 6)),
        # Outside the window — must NOT be counted
        ("PRESENT", date(2026, 5, 1)),
    ]
    for status, d in rows:
        db.add(AttendanceRecord(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            student_id=student.id,
            class_id=test_class.id,
            date=d,
            status=status,
            recorded_by=test_admin.id,
        ))
    await db.commit()

    summary = await terms.get_attendance_summary(
        db, test_tenant.id, student.id,
        start=date(2026, 1, 15), end=date(2026, 4, 2),
    )
    assert summary.present == 2
    assert summary.absent == 1
    assert summary.late == 1
    assert summary.excused == 1
    assert summary.total == 5


@pytest.mark.asyncio
async def test_term_report_context_bundles_everything(
    db: AsyncSession, test_tenant: Tenant, test_class, test_admin
):
    student = Student(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Bundle",
        last_name="Test",
        class_id=test_class.id,
        enrollment_date=date(2026, 1, 1),
        is_active=True,
    )
    db.add(student)
    await db.commit()

    term = terms.AcademicTerm(
        id="t2-2026", number=2, year=2026, label="Term 2 2026",
        start=date(2026, 5, 1), end=date(2026, 8, 10),
    )
    await terms.upsert_term(db, test_tenant.id, term)

    db.add(AttendanceRecord(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        student_id=student.id,
        class_id=test_class.id,
        date=date(2026, 5, 15),
        status="PRESENT",
        recorded_by=test_admin.id,
    ))
    await db.commit()

    ctx = await terms.term_report_context(
        db, test_tenant.id, student.id, "t2-2026",
    )
    assert ctx is not None
    assert ctx["term_number"] == 2
    assert ctx["academic_year"] == 2026
    assert ctx["term_label"] == "Term 2 2026"
    assert ctx["attendance"]["present"] == 1
    assert ctx["attendance"]["total"] == 1


@pytest.mark.asyncio
async def test_term_report_context_returns_none_for_unknown_term(
    db: AsyncSession, test_tenant: Tenant, test_class
):
    student = Student(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Nope",
        last_name="Test",
        class_id=test_class.id,
        enrollment_date=date(2026, 1, 1),
        is_active=True,
    )
    db.add(student)
    await db.commit()

    ctx = await terms.term_report_context(
        db, test_tenant.id, student.id, "nonexistent-term",
    )
    assert ctx is None
