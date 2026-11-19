"""Exam service — create exams, schedule papers, mark results, and
feed finalised marks into end-of-term report cards.

An exam belongs to one class + one (year, term). Creating it
auto-seeds an ExamSubject row per subject that class takes, taking
the total_marks from ClassSubject.effective_total_marks (so a /20
Shona Reading paper shows /20 on the mark sheet, not /100).

Marking a paper = upserting ExamResult rows. Percentage + grade
letter are computed at write-time from the exam's grading system
(or the class's resolved one), so later readers don't have to re-
run the math.

Finalisation copies results into any pending end-of-term REPORT_CARD
for the matching class + template under the ACADEMIC_GRADES section.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.exceptions import ConflictException, NotFoundException, ValidationException
from app.models.academic import ClassSubject, GradingSystem
from app.models.exam import Exam, ExamResult, ExamStatus, ExamSubject
from app.models.report import DailyReport, ReportTemplate
from app.models.school_class import SchoolClass
from app.models.student import Student
from app.services.mis_service import classify_percentage
from app.utils.tenant_context import (
    get_current_user_id_or_none,
    get_tenant_id,
)

logger = logging.getLogger(__name__)


class ExamService:

    # ------------------------------------------------------------------
    # Exam CRUD
    # ------------------------------------------------------------------

    async def list_exams(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        academic_year: str | None = None,
        term: int | None = None,
    ) -> list[Exam]:
        tenant_id = get_tenant_id()
        stmt = select(Exam).where(
            Exam.tenant_id == tenant_id,
            Exam.deleted_at.is_(None),
        )
        if class_id:
            stmt = stmt.where(Exam.class_id == class_id)
        if academic_year:
            stmt = stmt.where(Exam.academic_year == academic_year)
        if term:
            stmt = stmt.where(Exam.term == term)
        stmt = stmt.order_by(
            Exam.academic_year.desc(), Exam.term.desc(), Exam.name.asc(),
        )
        rows = (await db.execute(stmt)).scalars().all()
        return list(rows)

    async def get_exam(self, db: AsyncSession, exam_id: uuid.UUID) -> Exam:
        tenant_id = get_tenant_id()
        row = (await db.execute(
            select(Exam).where(
                Exam.id == exam_id,
                Exam.tenant_id == tenant_id,
                Exam.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
        if not row:
            raise NotFoundException("Exam")
        return row

    async def create_exam(
        self,
        db: AsyncSession,
        name: str,
        class_id: uuid.UUID,
        academic_year: str,
        term: int,
        report_template_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> Exam:
        """Create the exam + auto-seed one ExamSubject per class subject.

        The class's resolved grading system is pinned on the exam so
        later results compute grade letters consistently even if the
        tenant default changes later.
        """
        tenant_id = get_tenant_id()
        if not name.strip():
            raise ValidationException([{"field": "name", "message": "Name is required."}])
        if term < 1 or term > 4:
            raise ValidationException([{"field": "term", "message": "Term must be 1-4."}])

        klass = await db.get(SchoolClass, class_id)
        if not klass or klass.tenant_id != tenant_id:
            raise NotFoundException("Class")

        # Resolve the grading system: class override → tenant default
        grading_system_id = klass.grading_system_id
        if grading_system_id is None:
            default_gs = (await db.execute(
                select(GradingSystem).where(
                    GradingSystem.tenant_id == tenant_id,
                    GradingSystem.is_default.is_(True),
                    GradingSystem.deleted_at.is_(None),
                )
            )).scalar_one_or_none()
            if default_gs:
                grading_system_id = default_gs.id

        # Prevent dupes: tenant + class + (year, term, name)
        existing = (await db.execute(
            select(Exam).where(
                Exam.tenant_id == tenant_id,
                Exam.class_id == class_id,
                Exam.academic_year == academic_year,
                Exam.term == term,
                Exam.name == name.strip(),
                Exam.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
        if existing:
            raise ConflictException(
                f"An exam named '{name}' already exists for this class and term."
            )

        exam = Exam(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            class_id=class_id,
            name=name.strip(),
            academic_year=academic_year,
            term=term,
            status=ExamStatus.DRAFT,
            report_template_id=report_template_id,
            grading_system_id=grading_system_id,
            notes=notes,
            created_by=get_current_user_id_or_none(),
        )
        db.add(exam)
        await db.flush()

        # Seed one ExamSubject row per class_subject with total_marks
        # from the class subject's effective total.
        class_subjects = (await db.execute(
            select(ClassSubject).where(
                ClassSubject.class_id == class_id,
            )
        )).scalars().all()
        for idx, cs in enumerate(class_subjects):
            total = cs.effective_total_marks
            db.add(ExamSubject(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                exam_id=exam.id,
                subject_id=cs.subject_id,
                class_subject_id=cs.id,
                total_marks=total,
                display_order=idx,
            ))
        await db.flush()
        await db.refresh(exam)
        return exam

    async def delete_exam(self, db: AsyncSession, exam_id: uuid.UUID) -> None:
        exam = await self.get_exam(db, exam_id)
        if exam.status == ExamStatus.FINALIZED:
            raise ValidationException([{
                "field": "status",
                "message": "Finalised exams can't be deleted. Reopen first.",
            }])
        exam.deleted_at = datetime.now(timezone.utc)
        await db.flush()

    # ------------------------------------------------------------------
    # ExamSubject (timetable + paper config)
    # ------------------------------------------------------------------

    async def update_exam_subject(
        self,
        db: AsyncSession,
        exam_subject_id: uuid.UUID,
        *,
        total_marks: int | None = None,
        pass_mark: int | None = None,
        duration_minutes: int | None = None,
        scheduled_at: datetime | None = None,
        room: str | None = None,
        invigilator_id: uuid.UUID | None = None,
        paper_name: str | None = None,
    ) -> ExamSubject:
        tenant_id = get_tenant_id()
        row = (await db.execute(
            select(ExamSubject).where(
                ExamSubject.id == exam_subject_id,
                ExamSubject.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if not row:
            raise NotFoundException("Exam subject")

        if total_marks is not None:
            if total_marks <= 0:
                raise ValidationException([{"field": "total_marks", "message": "Must be > 0."}])
            row.total_marks = total_marks
        if pass_mark is not None:
            row.pass_mark = pass_mark if pass_mark > 0 else None
        if duration_minutes is not None:
            row.duration_minutes = duration_minutes if duration_minutes > 0 else None
        if scheduled_at is not None:
            row.scheduled_at = scheduled_at
        if room is not None:
            row.room = room.strip() or None
        if invigilator_id is not None:
            row.invigilator_id = invigilator_id
        if paper_name is not None:
            row.paper_name = paper_name.strip() or None

        # Bump exam status to SCHEDULED when the first paper gets a date
        if scheduled_at is not None and row.exam.status == ExamStatus.DRAFT:
            row.exam.status = ExamStatus.SCHEDULED

        await db.flush()
        await db.refresh(row)
        return row

    # ------------------------------------------------------------------
    # ExamResult (marking)
    # ------------------------------------------------------------------

    async def save_results(
        self,
        db: AsyncSession,
        exam_subject_id: uuid.UUID,
        rows: list[dict],
    ) -> list[ExamResult]:
        """Upsert a batch of (student_id, marks_obtained, is_absent,
        remarks) rows. Computes percentage + grade_letter at write
        time against the exam's grading scale, so later readers don't
        have to recompute.
        """
        tenant_id = get_tenant_id()
        es = (await db.execute(
            select(ExamSubject).where(
                ExamSubject.id == exam_subject_id,
                ExamSubject.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if not es:
            raise NotFoundException("Exam subject")

        exam = es.exam
        if exam.status == ExamStatus.FINALIZED:
            raise ValidationException([{
                "field": "status",
                "message": "Exam is finalised. Reopen to re-mark.",
            }])

        # Grading bands for the exam
        bands: list[dict] | None = None
        if exam.grading_system and exam.grading_system.grades:
            bands = list(exam.grading_system.grades)

        total_marks = es.total_marks
        marker_id = get_current_user_id_or_none()
        now = datetime.now(timezone.utc)

        # Index existing results by student_id so one query handles
        # the entire batch regardless of insert vs update.
        existing_rows = (await db.execute(
            select(ExamResult).where(
                ExamResult.exam_subject_id == exam_subject_id,
                ExamResult.tenant_id == tenant_id,
            )
        )).scalars().all()
        existing = {r.student_id: r for r in existing_rows}

        out: list[ExamResult] = []
        for row in rows:
            try:
                sid = uuid.UUID(str(row.get("student_id")))
            except (TypeError, ValueError):
                continue

            marks_raw = row.get("marks_obtained")
            is_absent = bool(row.get("is_absent"))
            remarks = (row.get("remarks") or None)

            marks: Decimal | None = None
            if not is_absent and marks_raw not in (None, ""):
                try:
                    marks = Decimal(str(marks_raw))
                except Exception:
                    raise ValidationException([{
                        "field": f"marks:{sid}",
                        "message": f"Marks must be numeric (got '{marks_raw}').",
                    }])
                if marks < 0 or marks > total_marks:
                    raise ValidationException([{
                        "field": f"marks:{sid}",
                        "message": f"Marks must be 0..{total_marks}.",
                    }])

            # Compute percentage + grade
            pct: Decimal | None = None
            grade: str | None = None
            if marks is not None and total_marks > 0:
                pct = (marks / Decimal(total_marks) * Decimal(100)).quantize(Decimal("0.01"))
                cls = classify_percentage(float(pct), bands)
                grade = cls.get("grade")

            er = existing.get(sid)
            if er is None:
                er = ExamResult(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    exam_subject_id=exam_subject_id,
                    student_id=sid,
                )
                db.add(er)
            er.marks_obtained = marks
            er.percentage = pct
            er.grade_letter = grade
            er.remarks = remarks
            er.is_absent = is_absent
            er.marked_by = marker_id
            er.marked_at = now
            out.append(er)

        # Flip exam status to IN_PROGRESS once results are being saved
        if exam.status in (ExamStatus.DRAFT, ExamStatus.SCHEDULED):
            exam.status = ExamStatus.IN_PROGRESS

        await db.flush()
        return out

    async def list_results(
        self,
        db: AsyncSession,
        exam_subject_id: uuid.UUID,
    ) -> list[ExamResult]:
        tenant_id = get_tenant_id()
        rows = (await db.execute(
            select(ExamResult).where(
                ExamResult.exam_subject_id == exam_subject_id,
                ExamResult.tenant_id == tenant_id,
            )
        )).scalars().all()
        return list(rows)

    # ------------------------------------------------------------------
    # Finalisation → feed into report cards
    # ------------------------------------------------------------------

    async def finalize_exam(
        self,
        db: AsyncSession,
        exam_id: uuid.UUID,
    ) -> dict:
        """Lock the exam and push every student's subject marks into
        a REPORT_CARD for the same (class, term, year).

        If exam.report_template_id is set, uses that template. Otherwise
        picks the first active REPORT_CARD template for the tenant.
        Creates draft reports where none exist, updates existing drafts;
        skips reports that are already FINALIZED (so historical data
        stays untouched).

        Returns a summary dict with counts the UI can toast back.
        """
        exam = await self.get_exam(db, exam_id)
        if exam.status == ExamStatus.FINALIZED:
            raise ValidationException([{
                "field": "status",
                "message": "Already finalised.",
            }])

        tenant_id = get_tenant_id()

        # Resolve template
        template = exam.report_template
        if template is None:
            template = (await db.execute(
                select(ReportTemplate).where(
                    ReportTemplate.tenant_id == tenant_id,
                    ReportTemplate.report_type == "REPORT_CARD",
                    ReportTemplate.is_active.is_(True),
                    ReportTemplate.deleted_at.is_(None),
                )
                .order_by(ReportTemplate.display_order.asc())
            )).scalars().first()
        if template is None:
            raise ValidationException([{
                "field": "template",
                "message": "No REPORT_CARD template on this tenant. Create one first.",
            }])

        # Locate the ACADEMIC_GRADES section on the template
        sections = list(template.sections or [])
        ag_section = next(
            (s for s in sections if s.get("type") == "ACADEMIC_GRADES"),
            None,
        )
        if not ag_section:
            raise ValidationException([{
                "field": "template",
                "message": "Template has no ACADEMIC_GRADES section.",
            }])
        section_id = ag_section.get("id")

        # Load every student in the class
        students = (await db.execute(
            select(Student).where(
                Student.tenant_id == tenant_id,
                Student.class_id == exam.class_id,
                Student.deleted_at.is_(None),
                Student.is_active.is_(True),
            )
        )).scalars().all()

        # Index results by (subject_id, student_id)
        exam_subjects = exam.exam_subjects
        es_by_subject = {es.subject_id: es for es in exam_subjects}
        all_results = (await db.execute(
            select(ExamResult).where(
                ExamResult.tenant_id == tenant_id,
                ExamResult.exam_subject_id.in_([es.id for es in exam_subjects]),
            )
        )).scalars().all()
        results_by_pair: dict[tuple[uuid.UUID, uuid.UUID], ExamResult] = {}
        for r in all_results:
            es = next(
                (x for x in exam_subjects if x.id == r.exam_subject_id),
                None,
            )
            if es:
                results_by_pair[(es.subject_id, r.student_id)] = r

        from datetime import date as _date
        report_date = _date(int(exam.academic_year),
                            {1: 3, 2: 6, 3: 9, 4: 11}[exam.term],
                            28)

        reports_created = 0
        reports_updated = 0
        reports_skipped = 0
        admin_id = get_current_user_id_or_none()

        for student in students:
            existing = (await db.execute(
                select(DailyReport).where(
                    DailyReport.tenant_id == tenant_id,
                    DailyReport.student_id == student.id,
                    DailyReport.template_id == template.id,
                    DailyReport.report_date == report_date,
                    DailyReport.deleted_at.is_(None),
                )
            )).scalar_one_or_none()

            if existing and existing.status == "FINALIZED":
                reports_skipped += 1
                continue

            # Build per-subject payload
            per_subject: dict[str, dict] = {}
            for es in exam_subjects:
                res = results_by_pair.get((es.subject_id, student.id))
                if res is None or res.marks_obtained is None:
                    continue
                per_subject[str(es.subject_id)] = {
                    "marks_obtained": float(res.marks_obtained),
                    "remarks": res.remarks or "",
                }
            data_payload = {"sections": {section_id: per_subject}}

            if existing:
                # Merge into existing data so other sections stay intact
                rd = dict(existing.report_data or {})
                sections_map = dict(rd.get("sections") or {})
                sections_map[section_id] = per_subject
                rd["sections"] = sections_map
                existing.report_data = rd
                flag_modified(existing, "report_data")
                reports_updated += 1
            else:
                db.add(DailyReport(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    student_id=student.id,
                    class_id=exam.class_id,
                    template_id=template.id,
                    report_date=report_date,
                    status="DRAFT",
                    report_data=data_payload,
                    created_by=admin_id,
                ))
                reports_created += 1

        exam.status = ExamStatus.FINALIZED
        exam.finalized_at = datetime.now(timezone.utc)
        await db.flush()

        return {
            "exam_id": str(exam.id),
            "reports_created": reports_created,
            "reports_updated": reports_updated,
            "reports_skipped_finalized": reports_skipped,
            "students_in_class": len(students),
        }

    async def reopen_exam(self, db: AsyncSession, exam_id: uuid.UUID) -> Exam:
        exam = await self.get_exam(db, exam_id)
        exam.status = ExamStatus.IN_PROGRESS
        exam.finalized_at = None
        await db.flush()
        await db.refresh(exam)
        return exam


_service: ExamService | None = None


def get_exam_service() -> ExamService:
    global _service
    if _service is None:
        _service = ExamService()
    return _service
