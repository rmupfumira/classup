"""Bulk import API endpoints."""

import logging
from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import SchoolClass
from app.schemas.common import APIResponse
from app.schemas.import_job import (
    ImportFieldInfo,
    ImportJobResponse,
    ImportJobStart,
    ImportPreviewResponse,
    ImportType,
)
from app.services.enrollment_template_service import get_enrollment_template_service
from app.services.import_service import IMPORT_FIELDS, get_import_service
from app.utils.permissions import require_role
from app.utils.tenant_context import get_tenant_id

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
    db: AsyncSession = Depends(get_db),
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


@router.get("/enrollment/template")
@require_role("SCHOOL_ADMIN", "TEACHER")
async def download_enrollment_template(
    class_id: UUID | None = Query(None, description="Pre-fill template with this class"),
    db: AsyncSession = Depends(get_db),
):
    """Download an Excel enrollment template."""
    tenant_id = get_tenant_id()
    template_service = get_enrollment_template_service()

    # Get active classes for this tenant
    result = await db.execute(
        select(SchoolClass).where(
            SchoolClass.tenant_id == tenant_id,
            SchoolClass.deleted_at.is_(None),
            SchoolClass.is_active.is_(True),
        ).order_by(SchoolClass.name)
    )
    classes = list(result.scalars().all())
    class_names = [c.name for c in classes]

    # If class_id specified, validate and get name for pre-fill
    prefill_class_name = None
    file_suffix = ""
    if class_id:
        target_class = next((c for c in classes if c.id == class_id), None)
        if not target_class:
            raise HTTPException(status_code=404, detail="Class not found")
        prefill_class_name = target_class.name
        file_suffix = f"_{target_class.name.replace(' ', '_')}"

    # Generate template
    excel_bytes = template_service.generate_template(
        class_names=class_names,
        prefill_class_name=prefill_class_name,
    )

    filename = f"student_enrollment{file_suffix}.xlsx"
    return StreamingResponse(
        BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/enrollment/upload", response_model=APIResponse)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def upload_enrollment(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a filled Excel enrollment template and process it."""
    service = get_import_service()

    # Validate file type
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(
            status_code=400,
            detail="File must be an Excel file (.xlsx). Please use the downloaded template.",
        )

    # Read file content
    try:
        file_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    # Process enrollment
    try:
        job = await service.create_and_process_enrollment(
            db,
            file_name=file.filename,
            file_bytes=file_bytes,
        )

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
                column_mapping={},
                created_by=job.created_by,
                completed_at=job.completed_at,
                created_at=job.created_at,
            ),
            message=f"Enrollment complete: {job.success_count} students created, {job.error_count} errors",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Enrollment upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process enrollment: {e}")


@router.post("/{job_id}/start", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def start_import(
    job_id: UUID,
    data: ImportJobStart,
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
