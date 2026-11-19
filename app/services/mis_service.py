"""MIS & Academic Analytics service — Phase 1.

Computes the top-level KPIs the Executive Dashboard shows. Everything
here derives from existing data (finalised REPORT_CARD reports +
attendance_records) — no new tables, no new migration.

Later phases build on this module:
  - subject performance analysis (Phase 2) will reuse
    ``_iter_student_percentages`` to produce per-subject rollups
  - longitudinal reports (Phase 4) will loop this per term
  - early-warning rules (Phase 5) will scan the output of
    ``kpi_summary`` to flag at-risk students
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.academic import Subject
from app.models.attendance import AttendanceRecord
from app.models.report import DailyReport, ReportTemplate
from app.models.school_class import SchoolClass
from app.models.student import Student
from app.utils.tenant_context import get_tenant_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Row DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StudentTermScore:
    """One student's aggregate % for one term (across all subjects they
    were graded on)."""

    student_id: uuid.UUID
    student_name: str
    class_id: uuid.UUID | None
    class_name: str | None
    total_obtained: float
    total_possible: float

    @property
    def percentage(self) -> float:
        return (
            (self.total_obtained / self.total_possible * 100.0)
            if self.total_possible > 0 else 0.0
        )


@dataclass(frozen=True)
class SubjectRow:
    """One subject's aggregate across the window."""

    subject_id: uuid.UUID
    subject_name: str
    student_count: int
    mean_percentage: float


@dataclass(frozen=True)
class ClassRow:
    """One class's aggregate across the window."""

    class_id: uuid.UUID
    class_name: str
    student_count: int
    mean_percentage: float


# ---------------------------------------------------------------------------
# Core pullers
# ---------------------------------------------------------------------------


class MisService:

    async def _finalized_report_cards(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
        class_id: uuid.UUID | None = None,
    ) -> list[DailyReport]:
        """Pull every finalised REPORT_CARD / PROGRESS_REPORT in the
        window. Daily activity reports are excluded — they have no
        ACADEMIC_GRADES section.
        """
        tenant_id = get_tenant_id()
        stmt = (
            select(DailyReport)
            .join(ReportTemplate, DailyReport.template_id == ReportTemplate.id)
            .where(
                DailyReport.tenant_id == tenant_id,
                DailyReport.deleted_at.is_(None),
                DailyReport.status == "FINALIZED",
                DailyReport.report_date >= from_date,
                DailyReport.report_date <= to_date,
                ReportTemplate.report_type.in_(["REPORT_CARD", "PROGRESS_REPORT"]),
            )
        )
        if class_id:
            stmt = stmt.where(DailyReport.class_id == class_id)

        return list((await db.execute(stmt)).scalars().all())

    async def _subjects_by_id(
        self, db: AsyncSession, subject_ids: Iterable[uuid.UUID],
    ) -> dict[uuid.UUID, Subject]:
        subject_ids = list({sid for sid in subject_ids if sid})
        if not subject_ids:
            return {}
        rows = (await db.execute(
            select(Subject).where(
                Subject.id.in_(subject_ids),
                Subject.deleted_at.is_(None),
            )
        )).scalars().all()
        return {s.id: s for s in rows}

    async def _students_meta(
        self, db: AsyncSession, student_ids: Iterable[uuid.UUID],
    ) -> dict[uuid.UUID, tuple[str, uuid.UUID | None, str | None]]:
        """Return {student_id: (full_name, class_id, class_name)}."""
        student_ids = list({sid for sid in student_ids if sid})
        if not student_ids:
            return {}
        rows = (await db.execute(
            select(Student.id, Student.first_name, Student.last_name,
                   Student.class_id, SchoolClass.name)
            .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
            .where(Student.id.in_(student_ids))
        )).all()
        out: dict[uuid.UUID, tuple[str, uuid.UUID | None, str | None]] = {}
        for sid, fn, ln, cid, cname in rows:
            out[sid] = (f"{fn} {ln}".strip(), cid, cname)
        return out

    async def _iter_student_percentages(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
        class_id: uuid.UUID | None = None,
    ) -> tuple[list[StudentTermScore], dict[uuid.UUID, dict]]:
        """For every finalised report in the window, walk the
        ACADEMIC_GRADES section and sum (marks_obtained / total) per
        student. Returns:

          - per-student total across all subjects
          - per-subject accumulator: {subject_id: {"obtained": .., "possible": .., "students": {sid, ..}}}

        The second output lets ``subject_rollup`` and the Phase-2 report
        reuse the same walk without re-querying.
        """
        reports = await self._finalized_report_cards(db, from_date, to_date, class_id)
        if not reports:
            return [], {}

        # Pre-cache templates so we can locate each report's ACADEMIC_GRADES section
        template_ids = {r.template_id for r in reports if r.template_id}
        templates = {
            t.id: t for t in (await db.execute(
                select(ReportTemplate).where(ReportTemplate.id.in_(template_ids))
            )).scalars().all()
        }

        per_student_total: defaultdict[uuid.UUID, dict] = defaultdict(
            lambda: {"obtained": 0.0, "possible": 0.0}
        )
        per_subject: defaultdict[uuid.UUID, dict] = defaultdict(
            lambda: {"obtained": 0.0, "possible": 0.0, "students": set()}
        )
        subject_ids_seen: set[uuid.UUID] = set()
        student_ids_seen: set[uuid.UUID] = set()

        for r in reports:
            tpl = templates.get(r.template_id)
            if not tpl or not tpl.sections:
                continue
            ag_section = next(
                (s for s in tpl.sections if s.get("type") == "ACADEMIC_GRADES"),
                None,
            )
            if not ag_section:
                continue
            section_id = ag_section.get("id")
            data = (r.report_data or {}).get("sections", {}).get(section_id, {})
            if not isinstance(data, dict) or not data:
                continue

            # Build a {subject_id → total_marks} lookup for this report
            subject_totals: dict[str, float] = {}
            for subj in ag_section.get("subjects", []) or []:
                sid = subj.get("id") or subj.get("subject_id")
                tot = subj.get("total_marks") or 100
                if sid:
                    subject_totals[str(sid)] = float(tot)

            student_ids_seen.add(r.student_id)
            for subj_id_str, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                marks = entry.get("marks_obtained")
                if marks in (None, ""):
                    continue
                try:
                    marks_f = float(marks)
                except (TypeError, ValueError):
                    continue
                total = float(subject_totals.get(subj_id_str, 100))
                if total <= 0:
                    continue
                per_student_total[r.student_id]["obtained"] += marks_f
                per_student_total[r.student_id]["possible"] += total
                try:
                    subj_uuid = uuid.UUID(subj_id_str)
                except ValueError:
                    continue
                per_subject[subj_uuid]["obtained"] += marks_f
                per_subject[subj_uuid]["possible"] += total
                per_subject[subj_uuid]["students"].add(r.student_id)
                subject_ids_seen.add(subj_uuid)

        meta = await self._students_meta(db, student_ids_seen)
        scores = [
            StudentTermScore(
                student_id=sid,
                student_name=meta.get(sid, ("Unknown", None, None))[0],
                class_id=meta.get(sid, ("", None, None))[1],
                class_name=meta.get(sid, ("", None, None))[2],
                total_obtained=totals["obtained"],
                total_possible=totals["possible"],
            )
            for sid, totals in per_student_total.items()
            if totals["possible"] > 0
        ]
        return scores, per_subject

    # ------------------------------------------------------------------
    # High-level rollups used by the Executive Dashboard
    # ------------------------------------------------------------------

    async def attendance_stats(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
        class_id: uuid.UUID | None = None,
    ) -> dict:
        """Return {present, absent, late, excused, total, rate_pct}."""
        tenant_id = get_tenant_id()
        stmt = (
            select(
                func.count(AttendanceRecord.id).label("total"),
                func.sum(
                    case(
                        (AttendanceRecord.status == "PRESENT", 1), else_=0
                    )
                ).label("present"),
                func.sum(
                    case(
                        (AttendanceRecord.status == "LATE", 1), else_=0
                    )
                ).label("late"),
                func.sum(
                    case(
                        (AttendanceRecord.status == "EXCUSED", 1), else_=0
                    )
                ).label("excused"),
                func.sum(
                    case(
                        (AttendanceRecord.status == "ABSENT", 1), else_=0
                    )
                ).label("absent"),
            )
            .where(
                AttendanceRecord.tenant_id == tenant_id,
                AttendanceRecord.date >= from_date,
                AttendanceRecord.date <= to_date,
            )
        )
        if class_id:
            stmt = stmt.where(AttendanceRecord.class_id == class_id)

        row = (await db.execute(stmt)).one()
        total = int(row.total or 0)
        present = int(row.present or 0)
        late = int(row.late or 0)
        excused = int(row.excused or 0)
        absent = int(row.absent or 0)
        # "Present" for the rate counts PRESENT + LATE + EXCUSED —
        # the student was accounted for. ABSENT is the only "not there".
        attended = present + late + excused
        rate = (attended / total * 100.0) if total else 0.0
        return {
            "total": total, "present": present, "late": late,
            "excused": excused, "absent": absent, "rate_pct": round(rate, 2),
        }

    async def chronic_absenteeism(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
        threshold_pct: float = 80.0,
    ) -> list[dict]:
        """Students whose attendance rate is below ``threshold_pct``.

        Chronic absenteeism = attended / total < threshold_pct (default 80%).
        """
        tenant_id = get_tenant_id()
        stmt = (
            select(
                Student.id,
                Student.first_name,
                Student.last_name,
                SchoolClass.name.label("class_name"),
                func.count(AttendanceRecord.id).label("total"),
                func.sum(
                    case(
                        (AttendanceRecord.status.in_(["PRESENT", "LATE", "EXCUSED"]), 1),
                        else_=0,
                    )
                ).label("attended"),
            )
            .select_from(Student)
            .join(AttendanceRecord, AttendanceRecord.student_id == Student.id)
            .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
            .where(
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
                AttendanceRecord.date >= from_date,
                AttendanceRecord.date <= to_date,
            )
            .group_by(
                Student.id, Student.first_name, Student.last_name,
                SchoolClass.name,
            )
        )
        rows = (await db.execute(stmt)).all()
        out: list[dict] = []
        for sid, fn, ln, cname, total, attended in rows:
            total = int(total or 0)
            attended = int(attended or 0)
            if total == 0:
                continue
            rate = attended / total * 100.0
            if rate < threshold_pct:
                out.append({
                    "student_id": str(sid),
                    "student_name": f"{fn} {ln}".strip(),
                    "class_name": cname,
                    "attendance_rate_pct": round(rate, 2),
                    "sessions": total,
                    "attended": attended,
                })
        # Lowest rate first — the people who need attention most
        out.sort(key=lambda x: x["attendance_rate_pct"])
        return out

    async def _class_subject_rollups(
        self,
        db: AsyncSession,
        scores: list[StudentTermScore],
        per_subject: dict[uuid.UUID, dict],
    ) -> tuple[list[ClassRow], list[SubjectRow]]:
        # ---- classes ----
        class_bucket: defaultdict[tuple[uuid.UUID | None, str | None], list[float]] = defaultdict(list)
        for s in scores:
            class_bucket[(s.class_id, s.class_name)].append(s.percentage)
        class_rows = [
            ClassRow(
                class_id=cid or uuid.UUID(int=0),
                class_name=cname or "(unassigned)",
                student_count=len(pcts),
                mean_percentage=round(sum(pcts) / len(pcts), 2) if pcts else 0.0,
            )
            for (cid, cname), pcts in class_bucket.items()
            if pcts
        ]
        class_rows.sort(key=lambda r: r.mean_percentage, reverse=True)

        # ---- subjects ----
        subj_meta = await self._subjects_by_id(db, per_subject.keys())
        subject_rows = []
        for sid, bucket in per_subject.items():
            possible = bucket["possible"]
            if possible <= 0:
                continue
            subject_rows.append(SubjectRow(
                subject_id=sid,
                subject_name=(subj_meta.get(sid).name if subj_meta.get(sid) else "(unknown)"),
                student_count=len(bucket["students"]),
                mean_percentage=round(bucket["obtained"] / possible * 100.0, 2),
            ))
        subject_rows.sort(key=lambda r: r.mean_percentage, reverse=True)

        return class_rows, subject_rows

    async def kpi_summary(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
        class_id: uuid.UUID | None = None,
        at_risk_avg_threshold: float = 50.0,
        at_risk_attendance_threshold: float = 80.0,
    ) -> dict:
        """One-shot: every KPI the Executive Dashboard needs.

        Not every KPI the spec names — just the ones we can derive from
        existing data in Phase 1. Phases 2-5 extend this with subject
        grade bands, early-warning rules, discipline incidents and
        longitudinal rank movement.
        """
        scores, per_subject = await self._iter_student_percentages(
            db, from_date, to_date, class_id,
        )
        total_students_with_marks = len(scores)
        overall_avg = (
            round(
                sum(s.percentage for s in scores) / total_students_with_marks, 2
            ) if total_students_with_marks else 0.0
        )
        # Pass rate uses 50% as the default pass mark — Zimsec O-Level
        # pass is 60% (C), but as the school-wide overall KPI 50% is a
        # safer generic. Per-subject analysis in Phase 2 will use the
        # grading system's pass mark.
        passing = sum(1 for s in scores if s.percentage >= 50.0)
        pass_rate = (passing / total_students_with_marks * 100.0) if total_students_with_marks else 0.0

        class_rows, subject_rows = await self._class_subject_rollups(
            db, scores, per_subject,
        )
        # top / bottom defined only when we have at least one row
        top_class = class_rows[0] if class_rows else None
        bottom_class = class_rows[-1] if (class_rows and len(class_rows) > 1) else None
        top_subject = subject_rows[0] if subject_rows else None
        bottom_subject = subject_rows[-1] if (subject_rows and len(subject_rows) > 1) else None

        attendance = await self.attendance_stats(db, from_date, to_date, class_id)
        chronic = await self.chronic_absenteeism(
            db, from_date, to_date, threshold_pct=at_risk_attendance_threshold,
        )

        # At-risk = low average OR chronic absenteeism (union, not AND).
        # Phase 5 replaces this with the configurable rules engine.
        chronic_student_ids = {c["student_id"] for c in chronic}
        at_risk_ids = {
            str(s.student_id) for s in scores if s.percentage < at_risk_avg_threshold
        } | chronic_student_ids

        return {
            "window": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "class_id": str(class_id) if class_id else None,
            },
            "academic": {
                "overall_average": overall_avg,
                "pass_rate_pct": round(pass_rate, 2),
                "pass_mark_pct": 50.0,
                "students_with_marks": total_students_with_marks,
                "top_class": (
                    {"class_id": str(top_class.class_id),
                     "class_name": top_class.class_name,
                     "mean_percentage": top_class.mean_percentage,
                     "student_count": top_class.student_count}
                    if top_class else None
                ),
                "bottom_class": (
                    {"class_id": str(bottom_class.class_id),
                     "class_name": bottom_class.class_name,
                     "mean_percentage": bottom_class.mean_percentage,
                     "student_count": bottom_class.student_count}
                    if bottom_class else None
                ),
                "top_subject": (
                    {"subject_id": str(top_subject.subject_id),
                     "subject_name": top_subject.subject_name,
                     "mean_percentage": top_subject.mean_percentage,
                     "student_count": top_subject.student_count}
                    if top_subject else None
                ),
                "bottom_subject": (
                    {"subject_id": str(bottom_subject.subject_id),
                     "subject_name": bottom_subject.subject_name,
                     "mean_percentage": bottom_subject.mean_percentage,
                     "student_count": bottom_subject.student_count}
                    if bottom_subject else None
                ),
            },
            "attendance": attendance,
            "at_risk": {
                "count": len(at_risk_ids),
                "chronic_absentees_count": len(chronic_student_ids),
                "chronic_absentees": chronic[:20],  # cap for the dashboard card
                "avg_threshold_pct": at_risk_avg_threshold,
                "attendance_threshold_pct": at_risk_attendance_threshold,
            },
            "class_rollup": [
                {"class_id": str(c.class_id), "class_name": c.class_name,
                 "student_count": c.student_count,
                 "mean_percentage": c.mean_percentage}
                for c in class_rows
            ],
            "subject_rollup": [
                {"subject_id": str(s.subject_id), "subject_name": s.subject_name,
                 "student_count": s.student_count,
                 "mean_percentage": s.mean_percentage}
                for s in subject_rows
            ],
        }


_service: MisService | None = None


def get_mis_service() -> MisService:
    global _service
    if _service is None:
        _service = MisService()
    return _service
