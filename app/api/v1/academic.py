"""API endpoints for managing subjects and grading systems."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.academic_service import get_academic_service
from app.utils.permissions import require_role

router = APIRouter()


# ==================== SCHEMAS ====================


class GradeDefinition(BaseModel):
    """A single grade level definition."""
    min: int = Field(..., ge=0, le=100)
    max: int = Field(..., ge=0, le=100)
    grade: str = Field(..., min_length=1, max_length=5)
    description: str = Field(..., min_length=1, max_length=50)
    points: float | None = None


class SubjectCreate(BaseModel):
    """Schema for creating a subject."""
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=1, max_length=20)
    description: str | None = None
    default_total_marks: int = Field(default=100, ge=1, le=1000)
    category: str | None = None
    display_order: int = 0


class SubjectUpdate(BaseModel):
    """Schema for updating a subject."""
    name: str | None = None
    code: str | None = None
    description: str | None = None
    default_total_marks: int | None = None
    category: str | None = None
    display_order: int | None = None
    is_active: bool | None = None


class SubjectResponse(BaseModel):
    """Schema for subject response."""
    id: uuid.UUID
    name: str
    code: str
    description: str | None
    default_total_marks: int
    category: str | None
    display_order: int
    is_active: bool

    class Config:
        from_attributes = True


class ClassSubjectAssign(BaseModel):
    """Schema for assigning a subject to a class."""
    subject_id: uuid.UUID
    total_marks: int | None = None
    is_compulsory: bool = True
    display_order: int = 0


class ClassSubjectUpdate(BaseModel):
    """Schema for updating a class-subject assignment."""
    total_marks: int | None = None
    is_compulsory: bool | None = None
    display_order: int | None = None


class ClassSubjectResponse(BaseModel):
    """Schema for class-subject response."""
    id: uuid.UUID
    class_id: uuid.UUID
    subject_id: uuid.UUID
    subject_name: str
    subject_code: str
    total_marks: int
    is_compulsory: bool
    display_order: int

    class Config:
        from_attributes = True


class GradingSystemCreate(BaseModel):
    """Schema for creating a grading system."""
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    grades: List[GradeDefinition]
    is_default: bool = False


class GradingSystemUpdate(BaseModel):
    """Schema for updating a grading system."""
    name: str | None = None
    description: str | None = None
    grades: List[GradeDefinition] | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class GradingSystemResponse(BaseModel):
    """Schema for grading system response."""
    id: uuid.UUID
    name: str
    description: str | None
    grades: List[dict]
    is_default: bool
    is_active: bool

    class Config:
        from_attributes = True


# ==================== SUBJECTS ====================


@router.get("/subjects", response_model=dict)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def list_subjects(
    category: str | None = None,
    is_active: bool | None = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all subjects for the current tenant."""
    service = get_academic_service()
    subjects, total = await service.get_subjects(
        db, category=category, is_active=is_active, page=page, page_size=page_size
    )

    return {
        "status": "success",
        "data": [SubjectResponse.model_validate(s) for s in subjects],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


@router.post("/subjects", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def create_subject(
    data: SubjectCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new subject."""
    service = get_academic_service()
    try:
        subject = await service.create_subject(
            db,
            name=data.name,
            code=data.code,
            description=data.description,
            default_total_marks=data.default_total_marks,
            category=data.category,
            display_order=data.display_order,
        )
        return {
            "status": "success",
            "data": SubjectResponse.model_validate(subject),
            "message": "Subject created successfully",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/subjects/{subject_id}", response_model=dict)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def get_subject(
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a subject by ID."""
    service = get_academic_service()
    subject = await service.get_subject(db, subject_id)
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    return {
        "status": "success",
        "data": SubjectResponse.model_validate(subject),
    }


@router.put("/subjects/{subject_id}", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def update_subject(
    subject_id: uuid.UUID,
    data: SubjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a subject."""
    service = get_academic_service()
    subject = await service.update_subject(
        db, subject_id, **data.model_dump(exclude_none=True)
    )
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    return {
        "status": "success",
        "data": SubjectResponse.model_validate(subject),
        "message": "Subject updated successfully",
    }


@router.delete("/subjects/{subject_id}", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def delete_subject(
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a subject."""
    service = get_academic_service()
    success = await service.delete_subject(db, subject_id)
    if not success:
        raise HTTPException(status_code=404, detail="Subject not found")

    return {
        "status": "success",
        "message": "Subject deleted successfully",
    }


# ==================== CLASS SUBJECTS ====================


@router.get("/classes/{class_id}/subjects", response_model=dict)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def list_class_subjects(
    class_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """List all subjects assigned to a class."""
    service = get_academic_service()
    class_subjects = await service.get_class_subjects(db, class_id)

    data = []
    for cs in class_subjects:
        data.append({
            "id": cs.id,
            "class_id": cs.class_id,
            "subject_id": cs.subject_id,
            "subject_name": cs.subject.name,
            "subject_code": cs.subject.code,
            "total_marks": cs.effective_total_marks,
            "is_compulsory": cs.is_compulsory,
            "display_order": cs.display_order,
        })

    return {
        "status": "success",
        "data": data,
    }


@router.post("/classes/{class_id}/subjects", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def assign_subject_to_class(
    class_id: uuid.UUID,
    data: ClassSubjectAssign,
    db: AsyncSession = Depends(get_db),
):
    """Assign a subject to a class."""
    service = get_academic_service()
    try:
        class_subject = await service.assign_subject_to_class(
            db,
            class_id=class_id,
            subject_id=data.subject_id,
            total_marks=data.total_marks,
            is_compulsory=data.is_compulsory,
            display_order=data.display_order,
        )
        return {
            "status": "success",
            "data": {
                "id": class_subject.id,
                "class_id": class_subject.class_id,
                "subject_id": class_subject.subject_id,
                "total_marks": class_subject.effective_total_marks,
                "is_compulsory": class_subject.is_compulsory,
                "display_order": class_subject.display_order,
            },
            "message": "Subject assigned to class successfully",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/classes/{class_id}/subjects/{subject_id}", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def update_class_subject(
    class_id: uuid.UUID,
    subject_id: uuid.UUID,
    data: ClassSubjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a class-subject assignment."""
    service = get_academic_service()

    # Find the class_subject by class_id and subject_id
    class_subjects = await service.get_class_subjects(db, class_id)
    class_subject = next((cs for cs in class_subjects if cs.subject_id == subject_id), None)

    if not class_subject:
        raise HTTPException(status_code=404, detail="Subject not assigned to this class")

    updated = await service.update_class_subject(
        db, class_subject.id, **data.model_dump(exclude_none=True)
    )

    return {
        "status": "success",
        "data": {
            "id": updated.id,
            "class_id": updated.class_id,
            "subject_id": updated.subject_id,
            "total_marks": updated.effective_total_marks,
            "is_compulsory": updated.is_compulsory,
            "display_order": updated.display_order,
        },
        "message": "Class subject updated successfully",
    }


@router.delete("/classes/{class_id}/subjects/{subject_id}", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def remove_subject_from_class(
    class_id: uuid.UUID,
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Remove a subject from a class."""
    service = get_academic_service()
    success = await service.remove_subject_from_class(db, class_id, subject_id)
    if not success:
        raise HTTPException(status_code=404, detail="Subject not assigned to this class")

    return {
        "status": "success",
        "message": "Subject removed from class successfully",
    }


@router.post("/classes/{class_id}/subjects/bulk", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def bulk_assign_subjects(
    class_id: uuid.UUID,
    subject_ids: List[uuid.UUID],
    db: AsyncSession = Depends(get_db),
):
    """Assign multiple subjects to a class at once."""
    service = get_academic_service()
    created = await service.bulk_assign_subjects_to_class(db, class_id, subject_ids)

    return {
        "status": "success",
        "message": f"{len(created)} subjects assigned to class",
        "data": {"assigned_count": len(created)},
    }


# ==================== GRADING SYSTEMS ====================


@router.get("/grading-systems", response_model=dict)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def list_grading_systems(
    is_active: bool | None = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all grading systems for the current tenant."""
    service = get_academic_service()
    systems, total = await service.get_grading_systems(
        db, is_active=is_active, page=page, page_size=page_size
    )

    return {
        "status": "success",
        "data": [GradingSystemResponse.model_validate(s) for s in systems],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


@router.post("/grading-systems", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def create_grading_system(
    data: GradingSystemCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new grading system."""
    service = get_academic_service()

    # Validate grades don't overlap
    grades_list = [g.model_dump() for g in data.grades]
    grades_list.sort(key=lambda x: x["min"])

    for i in range(len(grades_list) - 1):
        if grades_list[i]["max"] >= grades_list[i + 1]["min"]:
            raise HTTPException(
                status_code=400, detail="Grade ranges must not overlap"
            )

    try:
        grading_system = await service.create_grading_system(
            db,
            name=data.name,
            description=data.description,
            grades=grades_list,
            is_default=data.is_default,
        )
        return {
            "status": "success",
            "data": GradingSystemResponse.model_validate(grading_system),
            "message": "Grading system created successfully",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/grading-systems/{grading_system_id}", response_model=dict)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def get_grading_system(
    grading_system_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a grading system by ID."""
    service = get_academic_service()
    grading_system = await service.get_grading_system(db, grading_system_id)
    if not grading_system:
        raise HTTPException(status_code=404, detail="Grading system not found")

    return {
        "status": "success",
        "data": GradingSystemResponse.model_validate(grading_system),
    }


@router.put("/grading-systems/{grading_system_id}", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def update_grading_system(
    grading_system_id: uuid.UUID,
    data: GradingSystemUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a grading system."""
    service = get_academic_service()

    update_data = data.model_dump(exclude_none=True)

    # Validate grades if provided
    if "grades" in update_data and update_data["grades"]:
        grades_list = [g.model_dump() if hasattr(g, 'model_dump') else g for g in update_data["grades"]]
        grades_list.sort(key=lambda x: x["min"])

        for i in range(len(grades_list) - 1):
            if grades_list[i]["max"] >= grades_list[i + 1]["min"]:
                raise HTTPException(
                    status_code=400, detail="Grade ranges must not overlap"
                )
        update_data["grades"] = grades_list

    grading_system = await service.update_grading_system(
        db, grading_system_id, **update_data
    )
    if not grading_system:
        raise HTTPException(status_code=404, detail="Grading system not found")

    return {
        "status": "success",
        "data": GradingSystemResponse.model_validate(grading_system),
        "message": "Grading system updated successfully",
    }


@router.delete("/grading-systems/{grading_system_id}", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def delete_grading_system(
    grading_system_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a grading system."""
    service = get_academic_service()
    success = await service.delete_grading_system(db, grading_system_id)
    if not success:
        raise HTTPException(status_code=404, detail="Grading system not found")

    return {
        "status": "success",
        "message": "Grading system deleted successfully",
    }


# ==================== SETUP ====================


@router.post("/setup-defaults", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def setup_default_academic_config(
    education_type: str = Query(..., description="Education type: PRIMARY_SCHOOL or HIGH_SCHOOL"),
    db: AsyncSession = Depends(get_db),
):
    """Set up default subjects and grading system for the school."""
    if education_type not in ("PRIMARY_SCHOOL", "HIGH_SCHOOL"):
        raise HTTPException(
            status_code=400,
            detail="Education type must be PRIMARY_SCHOOL or HIGH_SCHOOL",
        )

    service = get_academic_service()

    # Create default subjects
    subjects = await service.setup_default_subjects(db, education_type)

    # Create default grading system
    grading_system = await service.setup_default_grading_system(db, education_type)

    return {
        "status": "success",
        "message": f"Created {len(subjects)} subjects and default grading system",
        "data": {
            "subjects_count": len(subjects),
            "grading_system_id": str(grading_system.id),
            "grading_system_name": grading_system.name,
        },
    }


# ==================== ACADEMIC TERMS ====================
# Owner directive 2026-10-06: let admins configure academic terms
# (number, year, start / end dates) so report cards can auto-fill
# "Term X, Academic Year YYYY, Days present/absent/late/excused".


class TermPayload(BaseModel):
    """Create / update an academic term."""
    id: str | None = Field(None, max_length=50)
    number: int = Field(..., ge=1, le=4)
    year: int = Field(..., ge=2000, le=2100)
    label: str | None = Field(None, max_length=100)
    start: str  # ISO date
    end: str    # ISO date


class TermResponse(BaseModel):
    id: str
    number: int
    year: int
    label: str
    start: str
    end: str


@router.get("/terms", response_model=dict)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def list_academic_terms(db: AsyncSession = Depends(get_db)):
    """List this tenant's configured academic terms, newest-first."""
    from app.services import academic_terms as _terms
    from app.utils.tenant_context import get_tenant_id

    terms = await _terms.list_terms(db, get_tenant_id())
    return {
        "status": "success",
        "data": [t.to_dict() for t in terms],
    }


@router.post("/terms", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def upsert_academic_term(
    body: TermPayload,
    db: AsyncSession = Depends(get_db),
):
    """Add or replace an academic term (keyed by id)."""
    from datetime import date as _date

    from app.services import academic_terms as _terms
    from app.utils.tenant_context import get_tenant_id

    try:
        start = _date.fromisoformat(body.start)
        end = _date.fromisoformat(body.end)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="start / end must be ISO dates (YYYY-MM-DD)",
        )

    term = _terms.AcademicTerm(
        id=body.id or f"t{body.number}-{body.year}",
        number=body.number,
        year=body.year,
        label=body.label or f"Term {body.number} {body.year}",
        start=start,
        end=end,
    )
    try:
        saved = await _terms.upsert_term(db, get_tenant_id(), term)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "status": "success",
        "data": saved.to_dict(),
        "message": "Term saved",
    }


@router.delete("/terms/{term_id}", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def delete_academic_term(term_id: str, db: AsyncSession = Depends(get_db)):
    """Delete an academic term by id."""
    from app.services import academic_terms as _terms
    from app.utils.tenant_context import get_tenant_id

    removed = await _terms.delete_term(db, get_tenant_id(), term_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Term not found")
    return {"status": "success", "message": "Term deleted"}


@router.get("/terms/{term_id}/student/{student_id}/summary", response_model=dict)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def preview_term_summary(
    term_id: str,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Preview the term summary (term meta + attendance totals) that
    report-card create will attach to ``report_data._term_summary``.

    Useful for the Create Report UI — the admin picks a term, this
    endpoint shows them what will auto-fill before they save.
    """
    from app.services import academic_terms as _terms
    from app.utils.tenant_context import get_tenant_id

    summary = await _terms.term_report_context(
        db, get_tenant_id(), student_id, term_id,
    )
    if not summary:
        raise HTTPException(status_code=404, detail="Term not found")
    return {"status": "success", "data": summary}


# ==================== CURRICULUM PACKS ====================
# Jurisdiction (country) → list of supported curricula → bulk-seed subjects
# into the tenant. The country comes from tenant.settings (super admin
# field); the admin here picks which curriculum to apply. Custom is
# always offered as a universal fallback so a tenant whose jurisdiction
# we don't have a pack for can still proceed.

@router.get("/curriculum-packs", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def list_curriculum_packs(db: AsyncSession = Depends(get_db)):
    """List curriculum packs available to the current tenant.

    The result's ``options`` list is filtered by the tenant's country
    (``tenant.settings.country`` → set by super admin) and always ends
    with the universal **Custom** option. ``active`` reports the pack
    the admin last applied, so the UI can preselect it.
    """
    from app.models import Tenant
    from app.utils.curriculum_packs import (
        CUSTOM_PACK_CODE,
        get_packs_for_country,
    )
    from app.utils.tenant_context import get_tenant_id

    tenant = await db.get(Tenant, get_tenant_id())
    country = (tenant.get_setting("country") if tenant else None) or ""
    packs = get_packs_for_country(country)

    options = [
        {
            "code": p.code,
            "name": p.name,
            "description": p.description,
            "subject_count": len(p.subjects),
        }
        for p in packs
    ]
    # Universal fallback — always present. Admin can pick this if
    # nothing fits their school.
    options.append({
        "code": CUSTOM_PACK_CODE,
        "name": "Custom",
        "description": "I'll set my subjects up manually.",
        "subject_count": 0,
    })

    active = tenant.get_setting("curriculum_pack") if tenant else None

    return {
        "status": "success",
        "data": {
            "country": country or None,
            "active": active,
            "options": options,
        },
    }


class CurriculumPackApply(BaseModel):
    """Request body for applying a curriculum pack."""
    pack_code: str = Field(..., min_length=1, max_length=64)


@router.post("/curriculum-packs/apply", response_model=dict)
@require_role("SCHOOL_ADMIN")
async def apply_curriculum_pack(
    body: CurriculumPackApply,
    db: AsyncSession = Depends(get_db),
):
    """Apply a curriculum pack: seed its subjects into the tenant and
    remember the choice.

    Idempotent — subjects whose ``code`` already exists on the tenant
    are skipped, not re-added. The response reports exactly what
    happened so the UI can show "Added 24 new subjects, skipped 6
    duplicates."
    """
    service = get_academic_service()
    result = await service.apply_curriculum_pack(db, body.pack_code)
    return {"status": "success", "data": result}
