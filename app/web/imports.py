"""Bulk import web routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user, get_templates
from app.services.import_service import IMPORT_FIELDS, get_import_service
from app.utils.permissions import require_role

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("", response_class=HTMLResponse)
@require_role("SCHOOL_ADMIN")
async def imports_list(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user),
    templates=Depends(get_templates),
):
    """List import jobs and upload new imports."""
    service = get_import_service()
    jobs, total = await service.list_jobs(db, page=1, page_size=20)

    return templates.TemplateResponse(
        "imports/list.html",
        {
            "request": request,
            "current_user": current_user,
            "jobs": jobs,
            "total": total,
        },
    )


@router.get("/upload", response_class=HTMLResponse)
@require_role("SCHOOL_ADMIN")
async def imports_upload(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user),
    templates=Depends(get_templates),
):
    """Upload page for bulk imports."""
    return templates.TemplateResponse(
        "imports/upload.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.get("/{job_id}", response_class=HTMLResponse)
@require_role("SCHOOL_ADMIN")
async def imports_detail(
    request: Request,
    job_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user),
    templates=Depends(get_templates),
):
    """View import job details and results."""
    from uuid import UUID

    service = get_import_service()
    job = await service.get_job(db, UUID(job_id))

    if not job:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "current_user": current_user},
            status_code=404,
        )

    return templates.TemplateResponse(
        "imports/results.html",
        {
            "request": request,
            "current_user": current_user,
            "job": job,
        },
    )


@router.get("/{job_id}/mapping", response_class=HTMLResponse)
@require_role("SCHOOL_ADMIN")
async def imports_mapping(
    request: Request,
    job_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user),
    templates=Depends(get_templates),
):
    """Column mapping page for import job."""
    from uuid import UUID

    service = get_import_service()
    job = await service.get_job(db, UUID(job_id))

    if not job:
        return templates.TemplateResponse(
            "errors/404.html",
            {"request": request, "current_user": current_user},
            status_code=404,
        )

    # Get available fields for this import type
    available_fields = IMPORT_FIELDS.get(job.import_type, {})

    # Get CSV headers from stored content
    headers = []
    sample_rows = []
    csv_content = job.column_mapping.get("_csv_content", "")
    if csv_content:
        import csv
        import io

        reader = csv.DictReader(io.StringIO(csv_content))
        headers = reader.fieldnames or []
        for i, row in enumerate(reader):
            if i < 3:
                sample_rows.append(dict(row))
            else:
                break

    return templates.TemplateResponse(
        "imports/mapping.html",
        {
            "request": request,
            "current_user": current_user,
            "job": job,
            "headers": headers,
            "sample_rows": sample_rows,
            "available_fields": available_fields,
        },
    )
