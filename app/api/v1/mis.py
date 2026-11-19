"""MIS / Academic Analytics API — Phase 1 Executive Dashboard KPIs."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse
from app.services.mis_service import get_mis_service
from app.utils.permissions import require_feature, require_role

router = APIRouter(tags=["MIS"])


@router.get("/mis/kpis")
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def kpi_summary(
    from_date: date,
    to_date: date,
    class_id: uuid.UUID | None = None,
    avg_threshold: float = Query(50.0, ge=0, le=100),
    attendance_threshold: float = Query(80.0, ge=0, le=100),
    _feat: None = Depends(require_feature("mis")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Everything the Executive Dashboard shows in one call.

    Thresholds are per-call parameters so Phase 5's rules engine can
    override them without an API change.
    """
    data = await get_mis_service().kpi_summary(
        db, from_date=from_date, to_date=to_date, class_id=class_id,
        at_risk_avg_threshold=avg_threshold,
        at_risk_attendance_threshold=attendance_threshold,
    )
    return APIResponse(status="success", data=data)


@router.get("/mis/subject-performance")
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def subject_performance(
    from_date: date,
    to_date: date,
    class_id: uuid.UUID | None = None,
    _feat: None = Depends(require_feature("mis")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Per-subject pass / fail / distinction / merit / credit rates +
    mean + median, scored against the class's (or tenant default)
    grading scale. Powers the Subject Performance page in Phase 2 and
    the grade distribution histogram in Phase 3.
    """
    data = await get_mis_service().subject_performance(
        db, from_date=from_date, to_date=to_date, class_id=class_id,
    )
    return APIResponse(status="success", data=data)


@router.get("/mis/grade-distribution")
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def grade_distribution(
    from_date: date,
    to_date: date,
    class_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    bin_size: int = Query(10, ge=1, le=50),
    _feat: None = Depends(require_feature("mis")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Histogram + bell-curve parameters for marks in the window."""
    data = await get_mis_service().grade_distribution(
        db, from_date=from_date, to_date=to_date,
        class_id=class_id, subject_id=subject_id, bin_size=bin_size,
    )
    return APIResponse(status="success", data=data)


@router.get("/mis/merit-list")
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def merit_list(
    from_date: date,
    to_date: date,
    scope: str = Query("SCHOOL", pattern="^(CLASS|GRADE_LEVEL|SCHOOL)$"),
    class_id: uuid.UUID | None = None,
    grade_level_id: uuid.UUID | None = None,
    top_n: int = Query(100, ge=1, le=1000),
    _feat: None = Depends(require_feature("mis")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Ranked student list — competition-style ties (1,1,3)."""
    data = await get_mis_service().merit_list(
        db, from_date=from_date, to_date=to_date, scope=scope,
        class_id=class_id, grade_level_id=grade_level_id, top_n=top_n,
    )
    return APIResponse(status="success", data=data)


@router.get("/mis/students/{student_id}/longitudinal")
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def student_longitudinal(
    student_id: uuid.UUID,
    from_date: date,
    to_date: date,
    _feat: None = Depends(require_feature("mis")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Full term-by-term trend for one student — aggregate % per term,
    per-subject movement, class rank + rank delta, strengths and
    weaknesses."""
    data = await get_mis_service().student_longitudinal(
        db, student_id=student_id,
        from_date=from_date, to_date=to_date,
    )
    return APIResponse(status="success", data=data)


@router.get("/mis/students/{student_id}/progress")
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def student_progress(
    student_id: uuid.UUID,
    from_date: date,
    to_date: date,
    _feat: None = Depends(require_feature("mis")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Current-vs-previous-term summary + attendance for one student.
    Shorter than the longitudinal view — same data condensed."""
    data = await get_mis_service().student_progress(
        db, student_id=student_id,
        from_date=from_date, to_date=to_date,
    )
    return APIResponse(status="success", data=data)


# ──────────── Phase 5 — Early Warning System ────────────

from pydantic import BaseModel


class EarlyWarningRules(BaseModel):
    """Configurable Early Warning thresholds. All optional — missing
    fields fall back to DEFAULT_RULES on the service."""

    aggregate_avg_critical: float | None = None
    aggregate_avg_high: float | None = None
    aggregate_avg_medium: float | None = None
    attendance_critical: float | None = None
    attendance_high: float | None = None
    attendance_medium: float | None = None
    decline_critical: float | None = None
    decline_high: float | None = None
    decline_medium: float | None = None


@router.get("/mis/early-warning/rules")
@require_role(Role.SCHOOL_ADMIN)
async def get_early_warning_rules(
    _feat: None = Depends(require_feature("mis")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Resolved Early Warning rules (tenant overrides + defaults)."""
    rules = await get_mis_service().get_early_warning_rules(db)
    return APIResponse(status="success", data=rules)


@router.put("/mis/early-warning/rules")
@require_role(Role.SCHOOL_ADMIN)
async def update_early_warning_rules(
    body: EarlyWarningRules,
    _feat: None = Depends(require_feature("mis")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Persist tenant-level rule overrides. Passes {} for a key clears
    the override and reverts to the default for that key."""
    # Keep only keys the caller actually set
    overrides = {k: v for k, v in body.model_dump().items() if v is not None}
    rules = await get_mis_service().save_early_warning_rules(db, overrides)
    await db.commit()
    return APIResponse(status="success", data=rules, message="Rules saved")


@router.get("/mis/early-warning/scan")
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def scan_early_warning(
    from_date: date,
    to_date: date,
    class_id: uuid.UUID | None = None,
    _feat: None = Depends(require_feature("mis")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Run the scanner and return every student the rules flagged,
    worst-level first, with their triggered-rule breakdown."""
    data = await get_mis_service().scan_at_risk_students(
        db, from_date=from_date, to_date=to_date, class_id=class_id,
    )
    return APIResponse(status="success", data=data)
