"""Settings web routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_templates
from app.models import Tenant
from app.utils.permissions import require_role
from app.utils.tenant_context import get_tenant_id

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_class=HTMLResponse)
@require_role("SCHOOL_ADMIN")
async def settings_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    templates=Depends(get_templates),
):
    """Settings overview - redirects to general settings."""
    return RedirectResponse(url="/settings/general", status_code=302)


@router.get("/general", response_class=HTMLResponse)
@require_role("SCHOOL_ADMIN")
async def settings_general(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    templates=Depends(get_templates),
):
    """General settings page."""
    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    return templates.TemplateResponse(
        "settings/general.html",
        {
            "request": request,
            "current_user": current_user,
            "tenant": tenant,
            "active_tab": "general",
        },
    )


@router.post("/general", response_class=HTMLResponse)
@require_role("SCHOOL_ADMIN")
async def settings_general_save(
    request: Request,
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    timezone: str = Form("Africa/Johannesburg"),
    language: str = Form("en"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Save general settings."""
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
@require_role("SCHOOL_ADMIN")
async def settings_features(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    templates=Depends(get_templates),
):
    """Features settings page."""
    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    features = (tenant.settings or {}).get("features", {})

    return templates.TemplateResponse(
        "settings/features.html",
        {
            "request": request,
            "current_user": current_user,
            "tenant": tenant,
            "features": features,
            "active_tab": "features",
        },
    )


@router.post("/features", response_class=HTMLResponse)
@require_role("SCHOOL_ADMIN")
async def settings_features_save(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Save features settings."""
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
@require_role("SCHOOL_ADMIN")
async def settings_terminology(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    templates=Depends(get_templates),
):
    """Terminology settings page."""
    tenant_id = get_tenant_id()
    tenant = await db.get(Tenant, tenant_id)

    terminology = (tenant.settings or {}).get("terminology", {})

    return templates.TemplateResponse(
        "settings/terminology.html",
        {
            "request": request,
            "current_user": current_user,
            "tenant": tenant,
            "terminology": terminology,
            "active_tab": "terminology",
        },
    )


@router.post("/terminology", response_class=HTMLResponse)
@require_role("SCHOOL_ADMIN")
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
    current_user=Depends(get_current_user),
):
    """Save terminology settings."""
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
@require_role("SCHOOL_ADMIN")
async def settings_webhooks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    templates=Depends(get_templates),
):
    """Webhooks settings page."""
    from app.services.webhook_service import get_webhook_service

    service = get_webhook_service()
    endpoints = await service.list_endpoints(db)

    return templates.TemplateResponse(
        "settings/webhooks.html",
        {
            "request": request,
            "current_user": current_user,
            "endpoints": endpoints,
            "active_tab": "webhooks",
        },
    )
