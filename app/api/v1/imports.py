"""Bulk import API endpoints."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.schemas.common import APIResponse
from app.schemas.import_job import (
    ImportFieldInfo,
    ImportJobResponse,
    ImportJobStart,
    ImportPreviewResponse,
    ImportType,
)
from app.services.import_service import IMPORT_FIELDS, get_import_service
from app.utils.permissions import require_role

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/fields", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def get_import_fields():
    """Get available fields for each import type."""
    fields = {}
    for import_type, field_defs in IMPORT_FIELDS.items():
        fields[import_type] = [
            ImportFieldInfo(
                name=name,
                label=info["label"],
                required=info.get("required", False),
            )
            for name, info in field_defs.items()
        ]

    return APIResponse(
        status="success",
        data=fields,
    )


@router.post("/upload", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def upload_csv(
    file: UploadFile = File(...),
    import_type: ImportType = Form(...),
    db: AsyncSession = Depends(get_db_session),
):
    """Upload a CSV file for import."""
    service = get_import_service()

    # Validate file type
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    # Read file content
    try:
        content = await file.read()
        csv_content = content.decode("utf-8-sig")  # Handle BOM
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    # Create job and get preview
    job, headers, sample_rows, total_rows = await service.create_job(
        db,
        file_name=file.filename,
        import_type=import_type.value,
        csv_content=csv_content,
    )

    # Get available fields for this import type
    available_fields = {
        import_type.value: list(IMPORT_FIELDS.get(import_type.value, {}).keys())
    }

    return APIResponse(
        status="success",
        data={
            "job_id": str(job.id),
            "preview": ImportPreviewResponse(
                headers=headers,
                sample_rows=sample_rows,
                total_rows=total_rows,
                available_fields=available_fields,
            ),
        },
        message="File uploaded. Please map columns and start import.",
    )


@router.post("/{job_id}/start", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def start_import(
    job_id: UUID,
    data: ImportJobStart,
    db: AsyncSession = Depends(get_db_session),
):
    """Start processing an import job with column mapping."""
    service = get_import_service()

    try:
        job = await service.start_import(db, job_id, data.column_mapping)

        return APIResponse(
            status="success",
            data=ImportJobResponse(
                id=job.id,
                tenant_id=job.tenant_id,
                import_type=job.import_type,
                file_name=job.file_name,
                status=job.status,
                total_rows=job.total_rows,
                processed_rows=job.processed_rows,
                success_count=job.success_count,
                error_count=job.error_count,
                errors=job.errors or [],
                column_mapping=job.column_mapping or {},
                created_by=job.created_by,
                completed_at=job.completed_at,
                created_at=job.created_at,
            ),
            message=f"Import completed: {job.success_count} succeeded, {job.error_count} failed",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{job_id}", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """Get an import job by ID."""
    service = get_import_service()

    job = await service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")

    # Don't expose CSV content in column_mapping
    column_mapping = {k: v for k, v in (job.column_mapping or {}).items() if k != "_csv_content"}

    return APIResponse(
        status="success",
        data=ImportJobResponse(
            id=job.id,
            tenant_id=job.tenant_id,
            import_type=job.import_type,
            file_name=job.file_name,
            status=job.status,
            total_rows=job.total_rows,
            processed_rows=job.processed_rows,
            success_count=job.success_count,
            error_count=job.error_count,
            errors=job.errors or [],
            column_mapping=column_mapping,
            created_by=job.created_by,
            completed_at=job.completed_at,
            created_at=job.created_at,
        ),
    )


@router.get("/{job_id}/errors", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def get_job_errors(
    job_id: UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """Get errors from an import job."""
    service = get_import_service()

    job = await service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")

    return APIResponse(
        status="success",
        data={"errors": job.errors or []},
    )


@router.get("", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def list_jobs(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db_session),
):
    """List import jobs."""
    service = get_import_service()

    jobs, total = await service.list_jobs(db, page=page, page_size=page_size)

    return APIResponse(
        status="success",
        data=[
            ImportJobResponse(
                id=job.id,
                tenant_id=job.tenant_id,
                import_type=job.import_type,
                file_name=job.file_name,
                status=job.status,
                total_rows=job.total_rows,
                processed_rows=job.processed_rows,
                success_count=job.success_count,
                error_count=job.error_count,
                errors=[],  # Don't include full errors in list
                column_mapping={},
                created_by=job.created_by,
                completed_at=job.completed_at,
                created_at=job.created_at,
            )
            for job in jobs
        ],
        pagination={
            "page": page,
            "page_size": page_size,
            "total_items": total,
            "total_pages": (total + page_size - 1) // page_size,
            "has_next": page * page_size < total,
            "has_prev": page > 1,
        },
    )
