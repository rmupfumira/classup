"""Academic settings web routes (subjects and grading systems)."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.academic_service import get_academic_service
from app.services.class_service import get_class_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)

router = APIRouter(prefix="/settings/academic")


async def _get_current_user(db: AsyncSession) -> User | None:
    """Get the current user from the database."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    auth_service = get_auth_service()
    try:
        return await auth_service.get_current_user(db, user_id)
    except Exception:
        return None


def _require_admin(user: User | None) -> RedirectResponse | None:
    """Check if user is admin, return redirect if not."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
        return RedirectResponse(url="/dashboard", status_code=302)
    return None


# ==================== SUBJECTS ====================


@router.get("/subjects", response_class=HTMLResponse)
async def subjects_list(
    request: Request,
    category: str | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the subjects management page."""
    user = await _get_current_user(db)
    redirect = _require_admin(user)
    if redirect:
        return redirect

    permissions = PermissionChecker(user.role)
    academic_service = get_academic_service()

    # Get subjects
    subjects, total = await academic_service.get_subjects(
        db, category=category, is_active=None, page=page, page_size=50
    )

    # Get unique categories for filter
    all_subjects, _ = await academic_service.get_subjects(db, is_active=None, page_size=200)
    categories = sorted(set(s.category for s in all_subjects if s.category))

    total_pages = (total + 49) // 50

    return templates.TemplateResponse(
        "settings/subjects/list.html",
        {
            "request": request,
            "user": user,
            "subjects": subjects,
            "categories": categories,
            "current_category": category,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/subjects/create", response_class=HTMLResponse)
async def subjects_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the subject creation page."""
    user = await _get_current_user(db)
    redirect = _require_admin(user)
    if redirect:
        return redirect

    permissions = PermissionChecker(user.role)

    # Get existing categories for suggestions
    academic_service = get_academic_service()
    all_subjects, _ = await academic_service.get_subjects(db, is_active=None, page_size=200)
    categories = sorted(set(s.category for s in all_subjects if s.category))

    return templates.TemplateResponse(
        "settings/subjects/form.html",
        {
            "request": request,
            "user": user,
            "subject": None,
            "categories": categories,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/subjects/{subject_id}/edit", response_class=HTMLResponse)
async def subjects_edit(
    request: Request,
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the subject edit page."""
    user = await _get_current_user(db)
    redirect = _require_admin(user)
    if redirect:
        return redirect

    permissions = PermissionChecker(user.role)
    academic_service = get_academic_service()

    subject = await academic_service.get_subject(db, subject_id)
    if not subject:
        return RedirectResponse(url="/settings/academic/subjects", status_code=302)

    # Get existing categories for suggestions
    all_subjects, _ = await academic_service.get_subjects(db, is_active=None, page_size=200)
    categories = sorted(set(s.category for s in all_subjects if s.category))

    return templates.TemplateResponse(
        "settings/subjects/form.html",
        {
            "request": request,
            "user": user,
            "subject": subject,
            "categories": categories,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


# ==================== GRADING SYSTEMS ====================


@router.get("/grading", response_class=HTMLResponse)
async def grading_systems_list(
    request: Request,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Render the grading systems management page."""
    user = await _get_current_user(db)
    redirect = _require_admin(user)
    if redirect:
        return redirect

    permissions = PermissionChecker(user.role)
    academic_service = get_academic_service()

    # Get grading systems
    grading_systems, total = await academic_service.get_grading_systems(
        db, is_active=None, page=page, page_size=50
    )

    total_pages = (total + 49) // 50

    return templates.TemplateResponse(
        "settings/grading/list.html",
        {
            "request": request,
            "user": user,
            "grading_systems": grading_systems,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/grading/create", response_class=HTMLResponse)
async def grading_systems_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the grading system creation page."""
    user = await _get_current_user(db)
    redirect = _require_admin(user)
    if redirect:
        return redirect

    permissions = PermissionChecker(user.role)

    return templates.TemplateResponse(
        "settings/grading/form.html",
        {
            "request": request,
            "user": user,
            "grading_system": None,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


@router.get("/grading/{grading_system_id}/edit", response_class=HTMLResponse)
async def grading_systems_edit(
    request: Request,
    grading_system_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the grading system edit page."""
    user = await _get_current_user(db)
    redirect = _require_admin(user)
    if redirect:
        return redirect

    permissions = PermissionChecker(user.role)
    academic_service = get_academic_service()

    grading_system = await academic_service.get_grading_system(db, grading_system_id)
    if not grading_system:
        return RedirectResponse(url="/settings/academic/grading", status_code=302)

    return templates.TemplateResponse(
        "settings/grading/form.html",
        {
            "request": request,
            "user": user,
            "grading_system": grading_system,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )


# ==================== CLASS SUBJECTS ====================


@router.get("/classes/{class_id}/subjects", response_class=HTMLResponse)
async def class_subjects_manage(
    request: Request,
    class_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Render the class subjects management page."""
    user = await _get_current_user(db)
    redirect = _require_admin(user)
    if redirect:
        return redirect

    permissions = PermissionChecker(user.role)
    academic_service = get_academic_service()
    class_service = get_class_service()

    # Get the class
    school_class = await class_service.get_class(db, class_id)
    if not school_class:
        return RedirectResponse(url="/classes", status_code=302)

    # Get assigned subjects
    class_subjects = await academic_service.get_class_subjects(db, class_id)

    # Get all available subjects
    all_subjects, _ = await academic_service.get_subjects(db, is_active=True, page_size=200)

    # Filter out already assigned subjects
    assigned_ids = {cs.subject_id for cs in class_subjects}
    available_subjects = [s for s in all_subjects if s.id not in assigned_ids]

    return templates.TemplateResponse(
        "settings/class_subjects.html",
        {
            "request": request,
            "user": user,
            "school_class": school_class,
            "class_subjects": class_subjects,
            "available_subjects": available_subjects,
            "current_language": get_current_language(),
            "permissions": permissions,
        },
    )
