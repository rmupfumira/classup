"""Populate the Gateway Academy tenant with realistic MIS demo data.

Idempotent — safe to re-run. Seeds:
  - ZIMSEC O-Level grading system (if missing) + made default
  - A REPORT_CARD template with an ACADEMIC_GRADES section
    pointing at every active subject
  - Three terms of finalised reports per student, with per-term
    + per-subject marks drawn from a wide-ish normal distribution
    so KPI tiles / distribution histograms / rankings have real
    movement to show
  - 40 attendance sessions per student spread across the last
    two months, weighted so most students are PRESENT but a few
    drift into chronic absenteeism (for the Early Warning page)

Run with: PYTHONPATH=. venv/Scripts/python.exe scripts/seed_mis_demo_data.py
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from app.database import async_session_factory
from app.models.academic import GradingSystem, Subject
from app.models.attendance import AttendanceRecord
from app.models.report import DailyReport, ReportTemplate
from app.models.school_class import SchoolClass
from app.models.student import Student
from app.models.tenant import Tenant
from app.models.user import User


TENANT_ADMIN_EMAIL = "admin@gatewayacademy.co.zw"

ZIMSEC_BANDS = [
    {"min": 80, "max": 100, "grade": "A", "description": "Distinction"},
    {"min": 70, "max": 79,  "grade": "B", "description": "Merit"},
    {"min": 60, "max": 69,  "grade": "C", "description": "Credit"},
    {"min": 50, "max": 59,  "grade": "D", "description": "Pass"},
    {"min": 0,  "max": 49,  "grade": "U", "description": "Fail"},
]


def q_date(year: int, quarter: int) -> date:
    """Mid-quarter date so _term_key_for_date rolls it into (year, Q)."""
    month = {1: 2, 2: 5, 3: 8, 4: 11}[quarter]
    return date(year, month, 15)


def student_target_mean(idx: int) -> float:
    """Pick a target mean per student so the overall class distribution
    is plausibly school-shaped: a handful of stars, a long middle
    cluster, a handful struggling.
    """
    # idx 0..17 → cluster around 50-85
    # Use the student index as a deterministic seed so re-runs don't
    # resurrect fresh randomness.
    rnd = random.Random(1000 + idx)
    return rnd.uniform(50, 85)


async def main() -> None:
    async with async_session_factory() as db:
        admin = (await db.execute(
            select(User).where(User.email == TENANT_ADMIN_EMAIL)
        )).scalar_one_or_none()
        if not admin:
            print(f"Admin {TENANT_ADMIN_EMAIL} not found — aborting")
            return
        tenant = await db.get(Tenant, admin.tenant_id)
        print(f"Seeding into: {tenant.name}")

        # ── 1. Grading system
        gs = (await db.execute(
            select(GradingSystem).where(
                GradingSystem.tenant_id == tenant.id,
                GradingSystem.name == "ZIMSEC O-Level (demo)",
                GradingSystem.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
        if gs is None:
            gs = GradingSystem(
                id=uuid.uuid4(), tenant_id=tenant.id,
                name="ZIMSEC O-Level (demo)",
                description="Seeded for the MIS walkthrough",
                is_default=True, is_active=True, grades=list(ZIMSEC_BANDS),
            )
            db.add(gs)
            # Clear is_default on anything else so our scale wins
            others = (await db.execute(
                select(GradingSystem).where(
                    GradingSystem.tenant_id == tenant.id,
                    GradingSystem.id != gs.id,
                )
            )).scalars().all()
            for o in others:
                o.is_default = False
            await db.flush()
            print("  + grading system")
        else:
            gs.is_default = True
            await db.flush()
            print("  = grading system (already present)")

        # ── 2. Classes + students
        classes = (await db.execute(
            select(SchoolClass).where(
                SchoolClass.tenant_id == tenant.id,
                SchoolClass.is_active == True,  # noqa: E712
                SchoolClass.deleted_at.is_(None),
            )
        )).scalars().all()
        students = (await db.execute(
            select(Student).where(
                Student.tenant_id == tenant.id,
                Student.is_active == True,  # noqa: E712
                Student.deleted_at.is_(None),
            )
        )).scalars().all()
        subjects = (await db.execute(
            select(Subject).where(
                Subject.tenant_id == tenant.id,
                Subject.is_active == True,  # noqa: E712
                Subject.deleted_at.is_(None),
            )
        )).scalars().all()
        print(f"  classes: {len(classes)}  students: {len(students)}  subjects: {len(subjects)}")
        if not students:
            print("  !! no students — add some first")
            return
        if not subjects:
            print("  !! no subjects — apply a curriculum pack first")
            return

        # ── 3. Report template
        section_id = "sec-mis-demo"
        template = (await db.execute(
            select(ReportTemplate).where(
                ReportTemplate.tenant_id == tenant.id,
                ReportTemplate.name == "MIS Demo Report Card",
                ReportTemplate.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
        if template is None:
            template = ReportTemplate(
                id=uuid.uuid4(), tenant_id=tenant.id,
                name="MIS Demo Report Card",
                description="Seeded for the MIS walkthrough",
                report_type="REPORT_CARD", frequency="TERMLY",
                sections=[{
                    "id": section_id, "type": "ACADEMIC_GRADES",
                    "title": "Academic grades", "display_order": 1,
                    "subjects": [
                        {"id": str(s.id), "name": s.name,
                         "total_marks": s.default_total_marks or 100}
                        for s in subjects
                    ],
                    "grading_system": ZIMSEC_BANDS,
                }],
                grading_system_id=gs.id,
                display_order=1,
                is_active=True,
            )
            db.add(template)
            await db.flush()
            print(f"  + template {template.id}")
        else:
            # Keep the template's subject list fresh in case the admin
            # added new subjects since the last run.
            sections = list(template.sections or [])
            if sections:
                sections[0]["subjects"] = [
                    {"id": str(s.id), "name": s.name,
                     "total_marks": s.default_total_marks or 100}
                    for s in subjects
                ]
                sections[0]["grading_system"] = ZIMSEC_BANDS
                sections[0]["id"] = section_id
            template.sections = sections
            template.grading_system_id = gs.id
            flag_modified(template, "sections")
            await db.flush()
            print("  = template (refreshed subjects)")

        # ── 4. Three terms of reports.
        year = date.today().year
        terms = [(year, 1), (year, 2), (year, 3)]

        # Only seed reports for students that actually belong to a class,
        # otherwise the daily_reports.class_id NOT NULL constraint fires.
        students_with_class = [s for s in students if s.class_id]
        print(f"  students eligible for reports (have class_id): {len(students_with_class)}")

        # Wipe prior demo-template reports so a corrected run doesn't
        # leave stale rows with the wrong scale lurking in the DB.
        from sqlalchemy import delete as sa_delete2
        await db.execute(sa_delete2(DailyReport).where(
            DailyReport.tenant_id == tenant.id,
            DailyReport.template_id == template.id,
        ))
        await db.flush()

        new_reports = 0
        for student_idx, student in enumerate(students_with_class):
            target_mean = student_target_mean(student_idx)
            # Per-student rng → stable marks across re-runs
            rnd = random.Random(2000 + student_idx)
            # Student's per-subject bias — some subjects suit them better
            subject_bias = {
                s.id: rnd.uniform(-10, 10) for s in subjects
            }
            # Terms climb or fall differently per student
            trend_slope = rnd.uniform(-3, 5)  # points per term
            for term_idx, (yy, q) in enumerate(terms):
                report_date = q_date(yy, q)
                # Check if a report already exists for this student/term/template
                existing = (await db.execute(
                    select(DailyReport).where(
                        DailyReport.tenant_id == tenant.id,
                        DailyReport.student_id == student.id,
                        DailyReport.template_id == template.id,
                        DailyReport.report_date == report_date,
                        DailyReport.deleted_at.is_(None),
                    )
                )).scalar_one_or_none()
                if existing is not None:
                    continue
                # Build per-subject marks — Gateway's subjects are
                # scored out of 10 or 20, so generate a % and then
                # scale it to each subject's own ``default_total_marks``.
                # Without this scaling, marks drawn as 50-80 explode to
                # 400-700% once the analytics layer divides by the real
                # total_marks for a /10 subject.
                subject_entries: dict = {}
                for subj in subjects:
                    base_pct = target_mean + subject_bias[subj.id] + (term_idx * trend_slope)
                    noisy_pct = rnd.gauss(base_pct, 8)
                    noisy_pct = max(5.0, min(98.0, noisy_pct))
                    subject_total = subj.default_total_marks or 100
                    # Round to 0.5 for scaled totals like 20, keep int for 100
                    if subject_total >= 50:
                        marks = round(noisy_pct * subject_total / 100.0)
                    else:
                        marks = round(noisy_pct * subject_total / 100.0 * 2) / 2
                    marks = max(0, min(subject_total, marks))
                    subject_entries[str(subj.id)] = {
                        "marks_obtained": marks,
                        "remarks": "",
                    }
                db.add(DailyReport(
                    id=uuid.uuid4(), tenant_id=tenant.id,
                    student_id=student.id, class_id=student.class_id,
                    template_id=template.id, report_date=report_date,
                    status="FINALIZED",
                    report_data={"sections": {section_id: subject_entries}},
                    created_by=admin.id,
                ))
                new_reports += 1
        print(f"  + {new_reports} finalised reports")

        # ── 5. Attendance records
        # Wipe + reseed the attendance window so re-runs give a clean
        # distribution. The IntegrityError on partial collisions isn't
        # worth coding around for a demo seeder.
        from sqlalchemy import delete as sa_delete
        today = date.today()
        session_dates = [today - timedelta(days=d) for d in range(0, 60, 2)]
        earliest = session_dates[-1]
        await db.execute(sa_delete(AttendanceRecord).where(
            AttendanceRecord.tenant_id == tenant.id,
            AttendanceRecord.date >= earliest,
        ))
        await db.flush()

        attendance_added = 0
        for student_idx, student in enumerate(students_with_class):
            rnd = random.Random(3000 + student_idx)
            target_attendance = rnd.uniform(60, 98)
            for d in session_dates[:40]:
                roll = rnd.uniform(0, 100)
                if roll <= target_attendance - 4:
                    status = "PRESENT"
                elif roll <= target_attendance:
                    status = "LATE"
                elif roll <= target_attendance + 2:
                    status = "EXCUSED"
                else:
                    status = "ABSENT"
                db.add(AttendanceRecord(
                    id=uuid.uuid4(), tenant_id=tenant.id,
                    student_id=student.id, class_id=student.class_id,
                    date=d, status=status, recorded_by=admin.id,
                ))
                attendance_added += 1
        print(f"  + {attendance_added} attendance records (wiped + reseeded)")

        # Make sure accounting / mis features are on
        settings = dict(tenant.settings or {})
        features = dict(settings.get("features") or {})
        features["mis"] = True
        settings["features"] = features
        tenant.settings = settings
        flag_modified(tenant, "settings")

        await db.commit()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
