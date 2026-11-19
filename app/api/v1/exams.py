"""Exams API — CRUD + marking + finalisation."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse
from app.services.exam_service import get_exam_service
from app.utils.permissions import require_feature, require_role

router = APIRouter(tags=["Exams"])


# ──────── Schemas ────────


class ExamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    class_id: uuid.UUID
    academic_year: str = Field(..., min_length=4, max_length=10)
    term: int = Field(..., ge=1, le=4)
    report_template_id: uuid.UUID | None = None
    notes: str | None = None


class ExamSubjectUpdate(BaseModel):
    paper_name: str | None = None
    total_marks: int | None = Field(None, gt=0)
    pass_mark: int | None = Field(None, ge=0)
    duration_minutes: int | None = Field(None, ge=0)
    scheduled_at: datetime | None = None
    room: str | None = Field(None, max_length=100)
    invigilator_id: uuid.UUID | None = None
    weight_percent: int | None = Field(None, ge=0, le=100)


class AddPaperRequest(BaseModel):
    subject_id: uuid.UUID
    paper_name: str | None = Field(None, max_length=100)
    total_marks: int = Field(100, gt=0)
    weight_percent: int = Field(50, ge=0, le=100)
    duration_minutes: int | None = Field(None, ge=0)


class ResultRow(BaseModel):
    student_id: uuid.UUID
    marks_obtained: float | None = None
    is_absent: bool = False
    remarks: str | None = None


class SaveResultsRequest(BaseModel):
    results: list[ResultRow]


# ──────── Response builders ────────


def _exam_subject_dict(es) -> dict:
    return {
        "id": str(es.id),
        "exam_id": str(es.exam_id),
        "subject_id": str(es.subject_id),
        "subject_name": es.subject.name if es.subject else "—",
        "subject_code": es.subject.code if es.subject else "",
        "paper_name": es.paper_name,
        "total_marks": es.total_marks,
        "pass_mark": es.pass_mark,
        "duration_minutes": es.duration_minutes,
        "scheduled_at": es.scheduled_at.isoformat() if es.scheduled_at else None,
        "room": es.room,
        "invigilator_id": str(es.invigilator_id) if es.invigilator_id else None,
        "invigilator_name": (
            f"{es.invigilator.first_name} {es.invigilator.last_name}"
            if es.invigilator else None
        ),
        "display_order": es.display_order,
        "weight_percent": es.weight_percent,
    }


def _exam_dict(exam, include_subjects: bool = True) -> dict:
    out = {
        "id": str(exam.id),
        "name": exam.name,
        "class_id": str(exam.class_id),
        "class_name": exam.school_class.name if exam.school_class else None,
        "academic_year": exam.academic_year,
        "term": exam.term,
        "status": exam.status,
        "report_template_id": (
            str(exam.report_template_id) if exam.report_template_id else None
        ),
        "report_template_name": (
            exam.report_template.name if exam.report_template else None
        ),
        "grading_system_id": (
            str(exam.grading_system_id) if exam.grading_system_id else None
        ),
        "grading_system_name": (
            exam.grading_system.name if exam.grading_system else None
        ),
        "notes": exam.notes,
        "finalized_at": exam.finalized_at.isoformat() if exam.finalized_at else None,
        "created_at": exam.created_at.isoformat() if exam.created_at else None,
    }
    if include_subjects:
        out["subjects"] = [_exam_subject_dict(es) for es in exam.exam_subjects]
    return out


def _result_dict(r) -> dict:
    return {
        "id": str(r.id),
        "exam_subject_id": str(r.exam_subject_id),
        "student_id": str(r.student_id),
        "student_name": (
            f"{r.student.first_name} {r.student.last_name}"
            if r.student else "—"
        ),
        "marks_obtained": float(r.marks_obtained) if r.marks_obtained is not None else None,
        "percentage": float(r.percentage) if r.percentage is not None else None,
        "grade_letter": r.grade_letter,
        "is_absent": r.is_absent,
        "remarks": r.remarks,
        "marked_by_name": (
            f"{r.marker.first_name} {r.marker.last_name}"
            if r.marker else None
        ),
        "marked_at": r.marked_at.isoformat() if r.marked_at else None,
    }


# ──────── Endpoints ────────


@router.get("/exams", response_model=APIResponse[list[dict]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def list_exams(
    class_id: uuid.UUID | None = None,
    academic_year: str | None = None,
    term: int | None = None,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    exams = await get_exam_service().list_exams(
        db, class_id=class_id, academic_year=academic_year, term=term,
    )
    return APIResponse(
        status="success",
        data=[_exam_dict(e, include_subjects=False) for e in exams],
    )


@router.get("/exams/{exam_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_exam(
    exam_id: uuid.UUID,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    exam = await get_exam_service().get_exam(db, exam_id)
    return APIResponse(status="success", data=_exam_dict(exam))


@router.post("/exams", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def create_exam(
    body: ExamCreate,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    exam = await get_exam_service().create_exam(
        db,
        name=body.name,
        class_id=body.class_id,
        academic_year=body.academic_year,
        term=body.term,
        report_template_id=body.report_template_id,
        notes=body.notes,
    )
    await db.commit()
    return APIResponse(
        status="success",
        data=_exam_dict(exam),
        message="Exam created. Schedule each paper to set the timetable.",
    )


@router.delete("/exams/{exam_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def delete_exam(
    exam_id: uuid.UUID,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    await get_exam_service().delete_exam(db, exam_id)
    await db.commit()
    return APIResponse(status="success", data={"deleted": True})


@router.put("/exams/subjects/{exam_subject_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def update_exam_subject(
    exam_subject_id: uuid.UUID,
    body: ExamSubjectUpdate,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    row = await get_exam_service().update_exam_subject(
        db, exam_subject_id,
        **body.model_dump(exclude_unset=True),
    )
    await db.commit()
    return APIResponse(status="success", data=_exam_subject_dict(row))


@router.get("/exams/subjects/{exam_subject_id}/results", response_model=APIResponse[list[dict]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def list_results(
    exam_subject_id: uuid.UUID,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    rows = await get_exam_service().list_results(db, exam_subject_id)
    return APIResponse(status="success", data=[_result_dict(r) for r in rows])


@router.post("/exams/subjects/{exam_subject_id}/results", response_model=APIResponse[list[dict]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def save_results(
    exam_subject_id: uuid.UUID,
    body: SaveResultsRequest,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    rows = await get_exam_service().save_results(
        db, exam_subject_id,
        rows=[r.model_dump() for r in body.results],
    )
    await db.commit()
    return APIResponse(
        status="success",
        data=[_result_dict(r) for r in rows],
        message=f"Saved {len(rows)} marks.",
    )


@router.post("/exams/{exam_id}/finalize", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def finalize_exam(
    exam_id: uuid.UUID,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    summary = await get_exam_service().finalize_exam(db, exam_id)
    await db.commit()
    return APIResponse(
        status="success",
        data=summary,
        message=(
            f"Finalised. {summary['reports_created']} new report cards drafted, "
            f"{summary['reports_updated']} existing drafts updated, "
            f"{summary['reports_skipped_finalized']} skipped (already finalised)."
        ),
    )


@router.post("/exams/{exam_id}/reopen", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def reopen_exam(
    exam_id: uuid.UUID,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    exam = await get_exam_service().reopen_exam(db, exam_id)
    await db.commit()
    return APIResponse(status="success", data=_exam_dict(exam))


# ──────── Multi-paper per subject ────────


@router.post("/exams/{exam_id}/apply-curriculum-papers", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def apply_curriculum_papers(
    exam_id: uuid.UUID,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Rebuild the exam's paper rows from the tenant's curriculum pack
    templates (ZIMSEC O-Level + Cambridge IGCSE today). Subjects with no
    template keep their existing single paper. Refuses to rebuild once
    any paper has results on it."""
    svc = get_exam_service()
    summary = await svc.apply_curriculum_papers(db, exam_id)
    await db.commit()
    exam = await svc.get_exam(db, exam_id)
    return APIResponse(
        status="success",
        data={**summary, "exam": _exam_dict(exam)},
        message=(
            f"Rebuilt {summary['papers_added']} papers from the "
            f"{summary['pack'] or 'generic'} curriculum."
        ),
    )


@router.post("/exams/{exam_id}/papers", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def add_paper(
    exam_id: uuid.UUID,
    body: AddPaperRequest,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Add one paper to an existing subject already on the exam."""
    es = await get_exam_service().add_paper(
        db, exam_id=exam_id,
        subject_id=body.subject_id,
        paper_name=body.paper_name,
        total_marks=body.total_marks,
        weight_percent=body.weight_percent,
        duration_minutes=body.duration_minutes,
    )
    await db.commit()
    return APIResponse(status="success", data=_exam_subject_dict(es))


@router.delete("/exams/subjects/{exam_subject_id}", response_model=APIResponse[dict])
@require_role(Role.SCHOOL_ADMIN)
async def delete_paper(
    exam_subject_id: uuid.UUID,
    _feat: None = Depends(require_feature("exam_management")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Remove a paper row. Refuses to touch a finalised exam."""
    await get_exam_service().delete_paper(db, exam_subject_id)
    await db.commit()
    return APIResponse(status="success", data={"deleted": True})
