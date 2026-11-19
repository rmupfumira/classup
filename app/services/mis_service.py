"""MIS & Academic Analytics service — Phases 1 + 2.

Phase 1 (``kpi_summary``) → Executive Dashboard KPIs.
Phase 2 (``subject_performance``) → per-subject pass / fail /
distinction / merit / credit rates + mean + median.

Everything here derives from existing data (finalised REPORT_CARD
reports + attendance_records) — no new tables, no new migration.

Later phases build on this module:
  - longitudinal reports (Phase 4) will loop this per term
  - early-warning rules (Phase 5) will scan the output of
    ``kpi_summary`` and ``subject_performance`` to flag at-risk
    students + subjects that need intervention
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

from app.models.academic import GradingSystem, Subject
from app.models.attendance import AttendanceRecord
from app.models.report import DailyReport, ReportTemplate
from app.models.school_class import SchoolClass
from app.models.student import Student
from app.utils.tenant_context import get_tenant_id


# ---------------------------------------------------------------------------
# Grading-band classification — shared by Phase 2 + Phase 3.
# ---------------------------------------------------------------------------

# Semantic bucket names. All grading scales we ship have at least five
# bands ordered high → low; the top band is always the distinction and
# the bottom is always the fail. The middle three are labelled by
# position so the labels mean the same thing to a parent on ZIMSEC vs
# Cambridge vs CAPS: "distinction = top band", "merit = second-top",
# "credit = third-top", "pass = anything above the fail band",
# "fail = lowest band".
BUCKET_DISTINCTION = "distinction"
BUCKET_MERIT = "merit"
BUCKET_CREDIT = "credit"
BUCKET_PASS = "pass"
BUCKET_FAIL = "fail"


def classify_percentage(pct: float, bands: list[dict] | None) -> dict:
    """Pick the band a % falls into, and tag it with a semantic bucket.

    ``bands`` is the grading system's ``grades`` array:
      [{min, max, grade, description, points?}, ...]
    Returns {"grade", "bucket", "description"}.

    When no bands are provided, falls back to a 50% pass mark and
    labels only pass/fail — that way Phase 2 still computes something
    usable on a tenant that hasn't configured a grading system.
    """
    if not bands:
        return {
            "grade": "-",
            "bucket": BUCKET_PASS if pct >= 50 else BUCKET_FAIL,
            "description": "Pass" if pct >= 50 else "Fail",
        }
    # Order high → low by the max boundary — matches how admins read
    # the scale. Stable sort so same-max bands keep their order.
    ordered = sorted(bands, key=lambda b: b.get("max", 0), reverse=True)
    match = next(
        (b for b in ordered if b.get("min", 0) <= pct <= b.get("max", 100)),
        ordered[-1],  # below the lowest band's min → treat as last band
    )
    # Bucket by POSITION in the ordered list — robust across naming
    # conventions ("Distinction" in ZIMSEC vs "Outstanding" in Cambridge).
    try:
        pos = ordered.index(match)
    except ValueError:
        pos = len(ordered) - 1
    bottom = len(ordered) - 1
    if pos == 0:
        bucket = BUCKET_DISTINCTION
    elif pos == 1:
        bucket = BUCKET_MERIT
    elif pos == 2:
        bucket = BUCKET_CREDIT
    elif pos == bottom:
        bucket = BUCKET_FAIL
    else:
        bucket = BUCKET_PASS
    return {
        "grade": match.get("grade", "-"),
        "bucket": bucket,
        "description": match.get("description", ""),
    }

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

    async def _resolve_grading_bands(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None,
    ) -> list[dict] | None:
        """Get the grading scale we should classify marks against.

        Reuses the chain unified in c1e85ae — class's grading system
        wins, otherwise tenant default. (The template.grading_system_id
        path lives in the report viewer and isn't reachable from
        analytics without knowing which template to pick; the class
        chain is what matters for subject-level rollups.)
        """
        from app.services.academic_service import get_academic_service
        academic = get_academic_service()
        gs = None
        if class_id:
            gs = await academic.resolve_class_grading_system(db, class_id)
        if not gs:
            gs = await academic.get_default_grading_system(db)
        if gs and gs.grades:
            return list(gs.grades)
        return None

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
        # per-subject accumulator carries the raw list of (student_id, pct)
        # tuples so Phase 2 can compute median + classify each one against
        # the grading bands.
        per_subject: defaultdict[uuid.UUID, dict] = defaultdict(
            lambda: {"obtained": 0.0, "possible": 0.0, "students": set(),
                     "student_percentages": []}
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
                per_subject[subj_uuid]["student_percentages"].append(
                    (r.student_id, marks_f / total * 100.0)
                )
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


    # ------------------------------------------------------------------
    # Phase 2 — Subject Performance Analysis
    # ------------------------------------------------------------------

    async def subject_performance(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
        class_id: uuid.UUID | None = None,
    ) -> dict:
        """Per-subject analysis for the given window.

        For every subject graded on at least one finalised report in
        the window, returns pass/fail/distinction/merit/credit rates
        + mean + median + grade-letter band counts for the histogram
        in Phase 3. The grading scale is resolved via the standard
        class → tenant-default chain so the numbers match what the
        parent sees on the report card.

        Output shape:
          {
            "grading_system": {"name", "bands": [...]},
            "pass_mark_pct": 50.0,   # default when no bands resolve
            "subjects": [
              {
                "subject_id", "subject_name",
                "student_count",
                "mean", "median",
                "rates": {
                   "distinction_pct", "merit_pct", "credit_pct",
                   "pass_pct", "fail_pct",
                },
                "band_counts": {"A": 5, "B": 7, "C": 4, ...},
                "best_student": {"id", "name", "percentage"},
                "worst_student": {"id", "name", "percentage"},
              },
              ...
            ]
          }
        """
        _scores, per_subject = await self._iter_student_percentages(
            db, from_date, to_date, class_id,
        )
        bands = await self._resolve_grading_bands(db, class_id)

        # The pass mark is the minimum of the lowest NON-fail band
        # (bucket != "fail"). Phase 2 needs a single number for the
        # overall pass_rate math. Falls back to 50 when no bands.
        pass_mark_pct = 50.0
        if bands:
            # Sort ascending by min to identify the fail band as the
            # lowest; the pass mark is the min of the next band up.
            asc = sorted(bands, key=lambda b: b.get("min", 0))
            if len(asc) >= 2:
                pass_mark_pct = float(asc[1].get("min", 50))

        from statistics import mean as _mean, median as _median

        subject_meta = await self._subjects_by_id(db, per_subject.keys())
        # Pull student names once for best/worst highlights
        all_student_ids: set[uuid.UUID] = set()
        for bucket in per_subject.values():
            all_student_ids.update(bucket["students"])
        student_meta = await self._students_meta(db, all_student_ids)

        subjects_out: list[dict] = []
        for sid, bucket in per_subject.items():
            pairs = bucket.get("student_percentages") or []
            if not pairs:
                continue
            # Use the best score per student in case a student has
            # multiple reports in the window — otherwise a student
            # appears twice and skews median.
            best_per_student: dict[uuid.UUID, float] = {}
            for st_id, pct in pairs:
                prev = best_per_student.get(st_id)
                if prev is None or pct > prev:
                    best_per_student[st_id] = pct
            pcts = list(best_per_student.values())
            if not pcts:
                continue

            # Classify each student's % into a bucket
            counters = {
                BUCKET_DISTINCTION: 0, BUCKET_MERIT: 0, BUCKET_CREDIT: 0,
                BUCKET_PASS: 0, BUCKET_FAIL: 0,
            }
            band_counts: dict[str, int] = {}
            for pct in pcts:
                c = classify_percentage(pct, bands)
                counters[c["bucket"]] = counters.get(c["bucket"], 0) + 1
                band_counts[c["grade"]] = band_counts.get(c["grade"], 0) + 1

            total = len(pcts)
            # Pass = anything except the fail bucket
            fail_count = counters[BUCKET_FAIL]
            pass_count = total - fail_count

            # Best + worst for this subject (not per-report — summarised
            # per-student so the UI can call out who to celebrate / help)
            ranked = sorted(best_per_student.items(), key=lambda kv: kv[1], reverse=True)
            best_sid, best_pct = ranked[0]
            worst_sid, worst_pct = ranked[-1]

            subjects_out.append({
                "subject_id": str(sid),
                "subject_name": (
                    subject_meta.get(sid).name
                    if subject_meta.get(sid) else "(unknown)"
                ),
                "student_count": total,
                "mean": round(_mean(pcts), 2),
                "median": round(_median(pcts), 2),
                "rates": {
                    "distinction_pct": round(counters[BUCKET_DISTINCTION] / total * 100.0, 2),
                    "merit_pct":       round(counters[BUCKET_MERIT]       / total * 100.0, 2),
                    "credit_pct":      round(counters[BUCKET_CREDIT]      / total * 100.0, 2),
                    "pass_pct":        round(pass_count                   / total * 100.0, 2),
                    "fail_pct":        round(fail_count                   / total * 100.0, 2),
                },
                "band_counts": band_counts,
                "best_student": {
                    "id": str(best_sid),
                    "name": student_meta.get(best_sid, ("Unknown", None, None))[0],
                    "percentage": round(best_pct, 2),
                },
                "worst_student": {
                    "id": str(worst_sid),
                    "name": student_meta.get(worst_sid, ("Unknown", None, None))[0],
                    "percentage": round(worst_pct, 2),
                },
            })

        # Pass rate desc — weakest subjects (high intervention need)
        # float to the bottom of the list, strongest to the top.
        subjects_out.sort(key=lambda s: s["rates"]["pass_pct"], reverse=True)

        return {
            "window": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "class_id": str(class_id) if class_id else None,
            },
            "grading_system": (
                {"bands": bands} if bands else None
            ),
            "pass_mark_pct": pass_mark_pct,
            "subjects": subjects_out,
        }


    # ------------------------------------------------------------------
    # Phase 4 — Longitudinal performance
    # ------------------------------------------------------------------

    def _term_key_for_date(self, d: date) -> tuple[int, int]:
        """Group a date into (year, term_number). Falls back to the
        calendar quarter when the tenant has no Zimbabwe/SA academic
        term boundaries set — same behaviour the dashboard uses for
        the "This term" preset.

        Returns (year, 1..4). The academic calendar service will
        later let us swap this for real term windows.
        """
        return (d.year, (d.month - 1) // 3 + 1)

    def _term_label(self, year: int, term: int) -> str:
        return f"Term {term} {year}"

    async def student_longitudinal(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        from_date: date,
        to_date: date,
    ) -> dict:
        """One student's performance across the terms in the window.

        Per term returns:
          - aggregate percentage
          - per-subject marks + percentage
          - rank + rank_of_total in the student's class
          - rank movement vs previous term (when there is one)

        Phase 5's Early Warning uses the delta field to flag declines
        of >=X% for a student.
        """
        tenant_id = get_tenant_id()
        # 1. Pull the student + their class so we can rank them later
        student = (await db.execute(
            select(Student).where(
                Student.id == student_id,
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
        if not student:
            return {
                "student_id": str(student_id),
                "student_name": None,
                "terms": [],
            }

        student_name = f"{student.first_name} {student.last_name}".strip()
        student_class_id = student.class_id

        # 2. Pull every finalised REPORT_CARD / PROGRESS_REPORT in the
        # window for the student's class (needed for rank). We'll filter
        # the student's own reports out of this bucket in a moment.
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
        if student_class_id:
            stmt = stmt.where(DailyReport.class_id == student_class_id)
        reports = list((await db.execute(stmt)).scalars().all())

        template_ids = {r.template_id for r in reports if r.template_id}
        templates = {
            t.id: t for t in (await db.execute(
                select(ReportTemplate).where(ReportTemplate.id.in_(template_ids))
            )).scalars().all()
        } if template_ids else {}

        # 3. Walk each report → (term_key, student_id, aggregate %,
        # per-subject %) so we can build per-term rollups.
        from collections import defaultdict

        # Per-term, per-student aggregated marks
        per_term_student: dict = defaultdict(
            lambda: defaultdict(lambda: {"obtained": 0.0, "possible": 0.0,
                                         "per_subject": {}})
        )
        # subject_id → name resolution
        subject_ids_seen: set[uuid.UUID] = set()

        for r in reports:
            tpl = templates.get(r.template_id)
            if not tpl or not tpl.sections:
                continue
            ag = next((s for s in tpl.sections if s.get("type") == "ACADEMIC_GRADES"), None)
            if not ag:
                continue
            section_id = ag.get("id")
            data = (r.report_data or {}).get("sections", {}).get(section_id, {})
            if not isinstance(data, dict) or not data:
                continue

            subject_totals: dict[str, float] = {}
            subject_names: dict[str, str] = {}
            for s in ag.get("subjects", []) or []:
                sid = s.get("id") or s.get("subject_id")
                tot = s.get("total_marks") or 100
                if sid:
                    subject_totals[str(sid)] = float(tot)
                    subject_names[str(sid)] = s.get("name") or ""

            term_key = self._term_key_for_date(r.report_date)
            bucket = per_term_student[term_key][r.student_id]
            for subj_id_str, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                marks = entry.get("marks_obtained")
                if marks in (None, ""):
                    continue
                try:
                    m = float(marks)
                except (TypeError, ValueError):
                    continue
                total = float(subject_totals.get(subj_id_str, 100))
                if total <= 0:
                    continue
                bucket["obtained"] += m
                bucket["possible"] += total
                try:
                    sid_uuid = uuid.UUID(subj_id_str)
                    subject_ids_seen.add(sid_uuid)
                except ValueError:
                    continue
                prev = bucket["per_subject"].get(subj_id_str)
                # If multiple reports for the same student / term / subject
                # exist, keep the best.
                pct = m / total * 100.0
                if prev is None or pct > prev["percentage"]:
                    bucket["per_subject"][subj_id_str] = {
                        "subject_id": subj_id_str,
                        "subject_name": subject_names.get(subj_id_str, ""),
                        "marks_obtained": m,
                        "total_marks": total,
                        "percentage": pct,
                    }

        # 4. Reduce each term into a per-student rank + aggregate
        bands = await self._resolve_grading_bands(db, student_class_id)
        ordered_terms = sorted(per_term_student.keys())  # (year, term) ascending
        timeline: list[dict] = []
        prev_rank: int | None = None
        prev_aggregate: float | None = None

        for term_key in ordered_terms:
            students_bucket = per_term_student[term_key]
            # Compute aggregate % for every student in the class for this term
            agg_by_student = {}
            for sid, data in students_bucket.items():
                if data["possible"] > 0:
                    agg_by_student[sid] = data["obtained"] / data["possible"] * 100.0
            # Rank in class — competition style
            ranked = sorted(agg_by_student.items(), key=lambda kv: kv[1], reverse=True)
            rank_of: dict[uuid.UUID, int] = {}
            last_pct = None
            current_rank = 0
            seen = 0
            for sid, pct in ranked:
                seen += 1
                if last_pct is None or pct != last_pct:
                    current_rank = seen
                    last_pct = pct
                rank_of[sid] = current_rank

            own = students_bucket.get(student_id)
            if not own or own["possible"] <= 0:
                continue
            aggregate = own["obtained"] / own["possible"] * 100.0
            classification = classify_percentage(aggregate, bands)

            # Movement vs previous term
            if prev_aggregate is not None:
                delta_pct = aggregate - prev_aggregate
            else:
                delta_pct = None
            rank_in_class = rank_of.get(student_id)
            if prev_rank is not None and rank_in_class is not None:
                rank_delta = prev_rank - rank_in_class  # positive = climbed
            else:
                rank_delta = None

            timeline.append({
                "year": term_key[0],
                "term": term_key[1],
                "label": self._term_label(*term_key),
                "aggregate_pct": round(aggregate, 2),
                "grade": classification["grade"],
                "bucket": classification["bucket"],
                "rank_in_class": rank_in_class,
                "class_size": len(agg_by_student),
                "aggregate_delta_pct": (round(delta_pct, 2) if delta_pct is not None else None),
                "rank_delta": rank_delta,
                "subjects": list(own["per_subject"].values()),
            })
            prev_aggregate = aggregate
            prev_rank = rank_in_class

        # 5. Subject strengths / weaknesses — top and bottom three
        # subjects across the whole window for this student.
        subj_totals: dict[str, dict] = {}
        for t in timeline:
            for s in t["subjects"]:
                acc = subj_totals.setdefault(s["subject_id"], {
                    "subject_id": s["subject_id"],
                    "subject_name": s["subject_name"],
                    "pcts": [],
                })
                acc["pcts"].append(s["percentage"])
        strength_source = []
        for sid, acc in subj_totals.items():
            if acc["pcts"]:
                mean_pct = sum(acc["pcts"]) / len(acc["pcts"])
                strength_source.append({
                    "subject_id": sid,
                    "subject_name": acc["subject_name"],
                    "mean_percentage": round(mean_pct, 2),
                    "term_count": len(acc["pcts"]),
                })
        strength_source.sort(key=lambda s: s["mean_percentage"], reverse=True)

        return {
            "student_id": str(student_id),
            "student_name": student_name,
            "class_id": str(student_class_id) if student_class_id else None,
            "window": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            },
            "terms": timeline,
            "strengths": strength_source[:3],
            "weaknesses": list(reversed(strength_source))[:3] if strength_source else [],
        }

    async def student_progress(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        from_date: date,
        to_date: date,
    ) -> dict:
        """Current-vs-previous-term summary + attendance for one student.

        Short-form view the Progress Report page uses. Longitudinal is
        the long form; this is the executive summary.
        """
        longi = await self.student_longitudinal(db, student_id, from_date, to_date)
        terms = longi.get("terms") or []
        current = terms[-1] if terms else None
        previous = terms[-2] if len(terms) >= 2 else None

        # Attendance for the same window, scoped to this student
        tenant_id = get_tenant_id()
        att_stmt = (
            select(
                func.count(AttendanceRecord.id).label("total"),
                func.sum(
                    case(
                        (AttendanceRecord.status.in_(["PRESENT", "LATE", "EXCUSED"]), 1),
                        else_=0,
                    )
                ).label("attended"),
            )
            .where(
                AttendanceRecord.tenant_id == tenant_id,
                AttendanceRecord.student_id == student_id,
                AttendanceRecord.date >= from_date,
                AttendanceRecord.date <= to_date,
            )
        )
        att_row = (await db.execute(att_stmt)).one()
        total = int(att_row.total or 0)
        attended = int(att_row.attended or 0)
        attendance_pct = (attended / total * 100.0) if total else 0.0

        return {
            "student_id": longi["student_id"],
            "student_name": longi["student_name"],
            "class_id": longi.get("class_id"),
            "current": current,
            "previous": previous,
            "aggregate_delta_pct": (
                round(current["aggregate_pct"] - previous["aggregate_pct"], 2)
                if current and previous else None
            ),
            "rank_delta": (
                (previous.get("rank_in_class") - current.get("rank_in_class"))
                if current and previous
                and current.get("rank_in_class") is not None
                and previous.get("rank_in_class") is not None
                else None
            ),
            "attendance": {
                "total": total,
                "attended": attended,
                "rate_pct": round(attendance_pct, 2),
            },
            "strengths": longi.get("strengths") or [],
            "weaknesses": longi.get("weaknesses") or [],
        }

    # ------------------------------------------------------------------
    # Phase 3 — Grade Distribution + Merit Lists
    # ------------------------------------------------------------------

    async def grade_distribution(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
        class_id: uuid.UUID | None = None,
        subject_id: uuid.UUID | None = None,
        bin_size: int = 10,
    ) -> dict:
        """Distribution of marks across the window.

        Returns a histogram + the parameters needed to draw a bell
        curve overlay (mean, stddev, n). When ``subject_id`` is set,
        the distribution is just that subject's scores — otherwise
        it's every per-student-per-subject score in scope.

        bin_size controls the histogram bucket width (default 10, so
        bins are 0-9, 10-19, …, 90-100). Last bucket is inclusive of
        100.
        """
        from statistics import mean as _mean, stdev as _stdev

        _scores, per_subject = await self._iter_student_percentages(
            db, from_date, to_date, class_id,
        )

        # Flatten into a single list of percentages — one entry per
        # (student, subject) pair, or filtered to one subject.
        pcts: list[float] = []
        if subject_id:
            bucket = per_subject.get(subject_id)
            if bucket:
                pcts = [p for _sid, p in bucket.get("student_percentages", [])]
        else:
            for bucket in per_subject.values():
                pcts.extend(p for _sid, p in bucket.get("student_percentages", []))

        bands = await self._resolve_grading_bands(db, class_id)

        # Build the percentage-range histogram
        bin_size = max(1, min(bin_size, 50))
        num_bins = (100 // bin_size) + (1 if 100 % bin_size else 0)
        hist_counts = [0] * num_bins
        for p in pcts:
            idx = min(int(p // bin_size), num_bins - 1)
            if p < 0:
                idx = 0
            hist_counts[idx] += 1

        total = len(pcts)
        histogram = []
        for i, c in enumerate(hist_counts):
            lo = i * bin_size
            hi = min(lo + bin_size - 1, 100)
            # Last bucket is inclusive of 100 to avoid a dead bucket
            if i == num_bins - 1:
                hi = 100
            histogram.append({
                "label": f"{lo}-{hi}%",
                "lo": lo,
                "hi": hi,
                "count": c,
                "percentage": round(c / total * 100.0, 2) if total else 0.0,
            })

        # Band-letter histogram — the "A/B/C/D/E/U" bars admins
        # actually recognise
        band_counts: dict[str, int] = {}
        band_totals: dict[str, int] = {}
        ordered_grades: list[str] = []
        if bands:
            ordered = sorted(bands, key=lambda b: b.get("max", 0), reverse=True)
            for b in ordered:
                g = b.get("grade", "-")
                band_counts[g] = 0
                band_totals[g] = 0
                if g not in ordered_grades:
                    ordered_grades.append(g)
        for p in pcts:
            cls = classify_percentage(p, bands)
            g = cls["grade"]
            band_counts[g] = band_counts.get(g, 0) + 1
            if g not in ordered_grades:
                ordered_grades.append(g)

        band_histogram = [
            {
                "grade": g,
                "count": band_counts.get(g, 0),
                "percentage": round(band_counts.get(g, 0) / total * 100.0, 2) if total else 0.0,
            }
            for g in ordered_grades
        ]

        # Bell curve parameters — mean + population stddev.
        # statistics.stdev needs 2+ samples; fall back to 0 when thin.
        mu = round(_mean(pcts), 2) if pcts else 0.0
        sigma = round(_stdev(pcts), 2) if len(pcts) >= 2 else 0.0

        # Pass mark for the shaded overlay
        pass_mark = 50.0
        if bands:
            asc = sorted(bands, key=lambda b: b.get("min", 0))
            if len(asc) >= 2:
                pass_mark = float(asc[1].get("min", 50))

        return {
            "window": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "class_id": str(class_id) if class_id else None,
                "subject_id": str(subject_id) if subject_id else None,
            },
            "n": total,
            "mean": mu,
            "stddev": sigma,
            "pass_mark_pct": pass_mark,
            "bin_size": bin_size,
            "histogram": histogram,
            "band_histogram": band_histogram,
        }

    async def merit_list(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
        scope: str = "SCHOOL",
        class_id: uuid.UUID | None = None,
        grade_level_id: uuid.UUID | None = None,
        top_n: int = 100,
    ) -> dict:
        """Ranked list of students by their aggregate % across all
        subjects they were graded on.

        scope ∈ {"CLASS", "GRADE_LEVEL", "SCHOOL"}. Rankings respect
        ties — two students with the same aggregate share a rank and
        the next distinct score skips the count, so a 1,1,3 ordering
        (not 1,1,2) is what shows up.

        For a weighted average later (Phase 5 config), swap the
        average builder in ``_iter_student_percentages`` for a
        subject-weighted version — the public merit_list shape stays
        the same.
        """
        # Scope-based filtering happens after the extraction so the
        # same walker handles CLASS / GRADE_LEVEL / SCHOOL cleanly.
        scores, _ = await self._iter_student_percentages(
            db,
            from_date,
            to_date,
            # Pass the class_id down when scope is CLASS so the query
            # itself stays cheap.
            class_id=class_id if scope == "CLASS" else None,
        )

        # GRADE_LEVEL needs class→grade_level resolution for every
        # student in scope. One query, in-memory filter.
        kept = scores
        if scope == "GRADE_LEVEL" and grade_level_id:
            class_ids = [s.class_id for s in scores if s.class_id]
            class_ids = list({cid for cid in class_ids})
            if class_ids:
                rows = (await db.execute(
                    select(SchoolClass.id, SchoolClass.grade_level_id)
                    .where(SchoolClass.id.in_(class_ids))
                )).all()
                in_grade = {
                    cid for (cid, gid) in rows
                    if gid == grade_level_id
                }
                kept = [s for s in scores if s.class_id in in_grade]

        # Rank highest first, break ties with earliest class listing
        kept = sorted(kept, key=lambda s: s.percentage, reverse=True)

        ranked: list[dict] = []
        prev_pct = None
        current_rank = 0
        seen = 0
        for s in kept:
            seen += 1
            # Competition-style ranking: tied students share rank,
            # the next distinct score takes the index they would have
            # occupied if there were no ties.
            if prev_pct is None or s.percentage != prev_pct:
                current_rank = seen
                prev_pct = s.percentage
            ranked.append({
                "rank": current_rank,
                "student_id": str(s.student_id),
                "student_name": s.student_name,
                "class_id": str(s.class_id) if s.class_id else None,
                "class_name": s.class_name,
                "mean_percentage": round(s.percentage, 2),
                "subjects_graded": None,  # Phase 2 output doesn't carry the count; Phase 4 will
            })
            if len(ranked) >= top_n:
                break

        return {
            "window": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "class_id": str(class_id) if class_id else None,
                "grade_level_id": str(grade_level_id) if grade_level_id else None,
            },
            "scope": scope,
            "total_in_scope": len(kept),
            "ranking": ranked,
        }


_service: MisService | None = None


def get_mis_service() -> MisService:
    global _service
    if _service is None:
        _service = MisService()
    return _service
