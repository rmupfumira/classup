"""Settings web routes."""

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Tenant
from app.services.auth_service import get_auth_service
from app.services.grade_level_service import get_grade_level_service
from app.templates_config import templates
from app.utils.tenant_context import (
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_class=HTMLResponse)
async def settings_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Settings overview - redirects to general settings."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    return RedirectResponse(url="/settings/general", status_code=302)


@router.get("/general", response_class=HTMLResponse)
async def settings_general(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """General settings page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    return templates.TemplateResponse(
        "settings/general.html",
        {
            "request": request,
            "user": current_user,
            "tenant": tenant,
            "active_tab": "general",
        },
    )


@router.post("/general", response_class=HTMLResponse)
async def settings_general_save(
    request: Request,
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    timezone: str = Form("Africa/Johannesburg"),
    language: str = Form("en"),
    db: AsyncSession = Depends(get_db),
):
    """Save general settings."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    if tenant:
        tenant.name = name
        tenant.email = email
        tenant.phone = phone
        tenant.address = address

        settings = tenant.settings or {}
        settings["timezone"] = timezone
        settings["language"] = language
        tenant.settings = settings

        await db.commit()

    return RedirectResponse(url="/settings/general?saved=1", status_code=302)


@router.get("/features", response_class=HTMLResponse)
async def settings_features(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Features settings page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    features = (tenant.settings or {}).get("features", {})

    return templates.TemplateResponse(
        "settings/features.html",
        {
            "request": request,
            "user": current_user,
            "tenant": tenant,
            "features": features,
            "active_tab": "features",
        },
    )


@router.post("/features", response_class=HTMLResponse)
async def settings_features_save(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save features settings."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    if tenant:
        form_data = await request.form()

        settings = tenant.settings or {}
        features = settings.get("features", {})

        # List of all toggleable features
        all_features = [
            "attendance_tracking",
            "messaging",
            "photo_sharing",
            "document_sharing",
            "daily_reports",
            "parent_communication",
            "nap_tracking",
            "bathroom_tracking",
            "fluid_tracking",
            "meal_tracking",
            "diaper_tracking",
            "homework_tracking",
            "grade_tracking",
            "behavior_tracking",
            "timetable_management",
            "subject_management",
            "exam_management",
            "disciplinary_records",
            "whatsapp_enabled",
        ]

        for feature in all_features:
            features[feature] = f"feature_{feature}" in form_data

        settings["features"] = features
        tenant.settings = settings
        await db.commit()

    return RedirectResponse(url="/settings/features?saved=1", status_code=302)


@router.get("/terminology", response_class=HTMLResponse)
async def settings_terminology(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Terminology settings page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    terminology = (tenant.settings or {}).get("terminology", {})

    return templates.TemplateResponse(
        "settings/terminology.html",
        {
            "request": request,
            "user": current_user,
            "tenant": tenant,
            "terminology": terminology,
            "active_tab": "terminology",
        },
    )


@router.post("/terminology", response_class=HTMLResponse)
async def settings_terminology_save(
    request: Request,
    student: str = Form("student"),
    students: str = Form("students"),
    teacher: str = Form("teacher"),
    teachers: str = Form("teachers"),
    class_term: str = Form("class"),
    classes: str = Form("classes"),
    parent: str = Form("parent"),
    parents: str = Form("parents"),
    db: AsyncSession = Depends(get_db),
):
    """Save terminology settings."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    if tenant:
        settings = tenant.settings or {}
        settings["terminology"] = {
            "student": student,
            "students": students,
            "teacher": teacher,
            "teachers": teachers,
            "class": class_term,
            "classes": classes,
            "parent": parent,
            "parents": parents,
        }
        tenant.settings = settings
        await db.commit()

    return RedirectResponse(url="/settings/terminology?saved=1", status_code=302)


@router.get("/webhooks", response_class=HTMLResponse)
async def settings_webhooks(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Webhooks settings page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    from app.services.webhook_service import get_webhook_service

    service = get_webhook_service()
    endpoints = await service.list_endpoints(db)

    return templates.TemplateResponse(
        "settings/webhooks.html",
        {
            "request": request,
            "user": current_user,
            "endpoints": endpoints,
            "active_tab": "webhooks",
        },
    )


# === Grade Levels ===


@router.get("/grade-levels", response_class=HTMLResponse)
async def settings_grade_levels(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Grade levels settings page."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    grade_level_service = get_grade_level_service()
    grade_levels, _ = await grade_level_service.get_grade_levels(db, is_active=None)

    return templates.TemplateResponse(
        "settings/grade_levels/list.html",
        {
            "request": request,
            "user": current_user,
            "grade_levels": grade_levels,
            "active_tab": "grade_levels",
        },
    )


@router.get("/grade-levels/create", response_class=HTMLResponse)
async def settings_grade_level_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Grade level create form."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    return templates.TemplateResponse(
        "settings/grade_levels/form.html",
        {
            "request": request,
            "user": current_user,
            "grade_level": None,
        },
    )


@router.get("/grade-levels/{grade_level_id}/edit", response_class=HTMLResponse)
async def settings_grade_level_edit(
    request: Request,
    grade_level_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Grade level edit form."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return RedirectResponse(url="/dashboard", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    grade_level_service = get_grade_level_service()
    grade_level = await grade_level_service.get_grade_level(db, grade_level_id)

    if not grade_level:
        return RedirectResponse(url="/settings/grade-levels", status_code=302)

    return templates.TemplateResponse(
        "settings/grade_levels/form.html",
        {
            "request": request,
            "user": current_user,
            "grade_level": grade_level,
        },
    )
