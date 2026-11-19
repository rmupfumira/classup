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
