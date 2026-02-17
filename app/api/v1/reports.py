"""Report API endpoints."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.report import (
    ReportCreate,
    ReportFinalize,
    ReportListResponse,
    ReportResponse,
    ReportTemplateCreate,
    ReportTemplateListResponse,
    ReportTemplateResponse,
    ReportTemplateUpdate,
    ReportUpdate,
)
from app.services.report_service import get_report_service
from app.utils.permissions import require_role

router = APIRouter()


# ============== Helper Functions ==============


def _build_report_response(report) -> dict:
    """Build a report response dict from a DailyReport model."""
    return {
        "id": report.id,
        "tenant_id": report.tenant_id,
        "student_id": report.student_id,
        "class_id": report.class_id,
        "template_id": report.template_id,
        "report_date": report.report_date,
        "report_data": report.report_data,
        "status": report.status,
        "finalized_at": report.finalized_at,
        "created_by": report.created_by,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
        "student": {
            "id": report.student.id,
            "first_name": report.student.first_name,
            "last_name": report.student.last_name,
            "photo_path": report.student.photo_path,
        } if report.student else None,
        "school_class": {
            "id": report.school_class.id,
            "name": report.school_class.name,
        } if report.school_class else None,
        "template": {
            "id": report.template.id,
            "name": report.template.name,
            "report_type": report.template.report_type,
        } if report.template else None,
        "created_by_user": {
            "id": report.created_by_user.id,
            "first_name": report.created_by_user.first_name,
            "last_name": report.created_by_user.last_name,
        } if report.created_by_user else None,
    }


def _build_template_response(template) -> dict:
    """Build a template response dict from a ReportTemplate model."""
    # Build grade_levels list from relationship
    grade_levels = []
    if hasattr(template, 'grade_levels') and template.grade_levels:
        grade_levels = [
            {"id": gl.id, "name": gl.name, "code": gl.code}
            for gl in template.grade_levels
        ]

    return {
        "id": template.id,
        "tenant_id": template.tenant_id,
        "name": template.name,
        "description": template.description,
        "report_type": template.report_type,
        "frequency": template.frequency,
        "applies_to_grade_level": template.applies_to_grade_level,  # DEPRECATED
        "grade_levels": grade_levels,
        "sections": template.sections,
        "display_order": template.display_order,
        "is_active": template.is_active,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def _build_template_list_item(template) -> dict:
    """Build a template list item response from a ReportTemplate model."""
    grade_levels = []
    if hasattr(template, 'grade_levels') and template.grade_levels:
        grade_levels = [
            {"id": gl.id, "name": gl.name, "code": gl.code}
            for gl in template.grade_levels
        ]

    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "report_type": template.report_type,
        "frequency": template.frequency,
        "applies_to_grade_level": template.applies_to_grade_level,  # DEPRECATED
        "grade_levels": grade_levels,
        "is_active": template.is_active,
        "section_count": len(template.sections) if template.sections else 0,
    }


# ============== Template Endpoints ==============


@router.get("/templates", response_model=APIResponse[list[ReportTemplateListResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def list_templates(
    is_active: bool | None = None,
    report_type: str | None = None,
    grade_level_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all report templates."""
    service = get_report_service()
    templates, total = await service.get_templates(
        db,
        is_active=is_active,
        report_type=report_type,
        grade_level_id=grade_level_id,
        page=page,
        page_size=page_size,
    )

    return {
        "status": "success",
        "data": [_build_template_list_item(t) for t in templates],
        "pagination": PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=(total + page_size - 1) // page_size,
            has_next=page * page_size < total,
            has_prev=page > 1,
        ),
    }


@router.post("/templates", response_model=APIResponse[ReportTemplateResponse])
@require_role(Role.SCHOOL_ADMIN)
async def create_template(
    data: ReportTemplateCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new report template."""
    service = get_report_service()
    template = await service.create_template(db, data)

    return {
        "status": "success",
        "data": _build_template_response(template),
        "message": "Template created successfully",
    }


@router.get("/templates/for-student/{student_id}", response_model=APIResponse[list[ReportTemplateListResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_templates_for_student(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get all applicable templates for a specific student."""
    service = get_report_service()
    templates = await service.get_templates_for_student(db, student_id)

    return {
        "status": "success",
        "data": [_build_template_list_item(t) for t in templates],
    }


@router.get("/templates/{template_id}", response_model=APIResponse[ReportTemplateResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def get_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific report template."""
    service = get_report_service()
    template = await service.get_template(db, template_id)

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "status": "success",
        "data": _build_template_response(template),
    }


@router.put("/templates/{template_id}", response_model=APIResponse[ReportTemplateResponse])
@require_role(Role.SCHOOL_ADMIN)
async def update_template(
    template_id: uuid.UUID,
    data: ReportTemplateUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a report template."""
    service = get_report_service()
    template = await service.update_template(db, template_id, data)

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "status": "success",
        "data": _build_template_response(template),
        "message": "Template updated successfully",
    }


@router.delete("/templates/{template_id}", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN)
async def delete_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a report template."""
    service = get_report_service()
    deleted = await service.delete_template(db, template_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "status": "success",
        "message": "Template deleted successfully",
    }


# ============== Report Endpoints ==============


@router.get("", response_model=APIResponse[list[ReportListResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def list_reports(
    class_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    template_id: uuid.UUID | None = None,
    report_date: date | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List reports with optional filters."""
    service = get_report_service()
    reports, total = await service.get_reports(
        db,
        class_id=class_id,
        student_id=student_id,
        template_id=template_id,
        report_date=report_date,
        status=status,
        page=page,
        page_size=page_size,
    )

    return {
        "status": "success",
        "data": reports,
        "pagination": PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=(total + page_size - 1) // page_size,
            has_next=page * page_size < total,
            has_prev=page > 1,
        ),
    }


@router.post("", response_model=APIResponse[ReportResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def create_report(
    data: ReportCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new report."""
    service = get_report_service()

    # Check if report already exists
    existing = await service.get_existing_report(
        db, data.student_id, data.template_id, data.report_date
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A report already exists for this student, template, and date",
        )

    report = await service.create_report(db, data)

    return {
        "status": "success",
        "data": _build_report_response(report),
        "message": "Report created successfully",
    }


@router.get("/student/{student_id}", response_model=APIResponse[list[ReportListResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_student_reports(
    student_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get all reports for a specific student."""
    # TODO: Add parent access check (parent can only see their own children)
    service = get_report_service()
    reports, total = await service.get_student_reports(
        db,
        student_id=student_id,
        page=page,
        page_size=page_size,
    )

    return {
        "status": "success",
        "data": reports,
        "pagination": PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=(total + page_size - 1) // page_size,
            has_next=page * page_size < total,
            has_prev=page > 1,
        ),
    }


@router.get("/stats", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN)
async def get_report_stats(
    class_id: uuid.UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get report statistics."""
    service = get_report_service()
    stats = await service.get_report_stats(
        db,
        class_id=class_id,
        start_date=start_date,
        end_date=end_date,
    )

    return {
        "status": "success",
        "data": stats,
    }


@router.get("/{report_id}", response_model=APIResponse[ReportResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific report."""
    # TODO: Add parent access check (parent can only see their own children's reports)
    service = get_report_service()
    report = await service.get_report(db, report_id)

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return {
        "status": "success",
        "data": _build_report_response(report),
    }


@router.put("/{report_id}", response_model=APIResponse[ReportResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def update_report(
    report_id: uuid.UUID,
    data: ReportUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a report (only if still draft)."""
    service = get_report_service()

    try:
        report = await service.update_report(db, report_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return {
        "status": "success",
        "data": _build_report_response(report),
        "message": "Report updated successfully",
    }


@router.post("/{report_id}/finalize", response_model=APIResponse[ReportResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def finalize_report(
    report_id: uuid.UUID,
    data: ReportFinalize = ReportFinalize(),
    db: AsyncSession = Depends(get_db),
):
    """Finalize a report and notify parents."""
    service = get_report_service()

    try:
        report = await service.finalize_report(
            db, report_id, notify_parents=data.notify_parents
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return {
        "status": "success",
        "data": _build_report_response(report),
        "message": "Report finalized successfully",
    }


@router.delete("/{report_id}", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def delete_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a report (only if still draft)."""
    service = get_report_service()

    try:
        deleted = await service.delete_report(db, report_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail="Report not found")

    return {
        "status": "success",
        "message": "Report deleted successfully",
    }
