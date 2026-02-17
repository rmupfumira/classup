"""Onboarding wizard web routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth_service import get_auth_service
from app.services.onboarding_service import get_onboarding_service
from app.templates_config import templates
from app.utils.tenant_context import get_current_user_id_or_none, get_current_user_role

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("", response_class=HTMLResponse)
async def onboarding_start(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Start the onboarding wizard (redirects to step 1)."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    return RedirectResponse(url="/onboarding/step1", status_code=302)


@router.get("/step1", response_class=HTMLResponse)
async def onboarding_step1(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Step 1: School Information."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    service = get_onboarding_service()
    status = await service.get_onboarding_status(db)

    return templates.TemplateResponse(
        "onboarding/step1_school_info.html",
        {
            "request": request,
            "current_user": current_user,
            "status": status,
            "step": 1,
        },
    )


@router.post("/step1", response_class=HTMLResponse)
async def onboarding_step1_submit(
    request: Request,
    name: str = Form(...),
    address: str = Form(""),
    phone: str = Form(""),
    timezone: str = Form("Africa/Johannesburg"),
    db: AsyncSession = Depends(get_db),
):
    """Submit step 1 and redirect to step 2."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    service = get_onboarding_service()

    await service.update_school_info(
        db,
        name=name,
        address=address,
        phone=phone,
        timezone=timezone,
    )

    return RedirectResponse(url="/onboarding/step2", status_code=302)


@router.get("/step2", response_class=HTMLResponse)
async def onboarding_step2(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Step 2: Education Type & Features."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    service = get_onboarding_service()
    status = await service.get_onboarding_status(db)

    return templates.TemplateResponse(
        "onboarding/step2_education_type.html",
        {
            "request": request,
            "current_user": current_user,
            "status": status,
            "step": 2,
        },
    )


@router.post("/step2", response_class=HTMLResponse)
async def onboarding_step2_submit(
    request: Request,
    education_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Submit step 2 and redirect to step 3."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    service = get_onboarding_service()

    # Get form data for features
    form_data = await request.form()
    features = [key for key in form_data.keys() if key.startswith("feature_")]
    features = [f.replace("feature_", "") for f in features]

    await service.update_education_type(
        db,
        education_type=education_type,
        enabled_features=features if features else None,
    )

    return RedirectResponse(url="/onboarding/step3", status_code=302)


@router.get("/step3", response_class=HTMLResponse)
async def onboarding_step3(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Step 3: Create Classes."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    service = get_onboarding_service()
    status = await service.get_onboarding_status(db)

    return templates.TemplateResponse(
        "onboarding/step3_classes.html",
        {
            "request": request,
            "current_user": current_user,
            "status": status,
            "step": 3,
        },
    )


@router.post("/step3", response_class=HTMLResponse)
async def onboarding_step3_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Submit step 3 and redirect to step 4."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    service = get_onboarding_service()

    # Parse class data from form
    form_data = await request.form()

    # Classes are submitted as class_name_1, class_name_2, etc.
    classes = []
    i = 1
    while f"class_name_{i}" in form_data:
        name = form_data.get(f"class_name_{i}")
        if name:
            grade_level_id = form_data.get(f"class_grade_level_id_{i}")
            classes.append({
                "name": name,
                "grade_level_id": grade_level_id if grade_level_id else None,
                "capacity": int(form_data.get(f"class_capacity_{i}") or 0) or None,
            })
        i += 1

    if classes:
        await service.create_classes(db, classes)

    return RedirectResponse(url="/onboarding/step4", status_code=302)


@router.get("/step4", response_class=HTMLResponse)
async def onboarding_step4(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Step 4: Invite Teachers."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    service = get_onboarding_service()
    status = await service.get_onboarding_status(db)

    return templates.TemplateResponse(
        "onboarding/step4_invite_teachers.html",
        {
            "request": request,
            "current_user": current_user,
            "status": status,
            "step": 4,
        },
    )


@router.post("/step4", response_class=HTMLResponse)
async def onboarding_step4_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Submit step 4 and redirect to step 5."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    service = get_onboarding_service()

    # Parse teacher data from form
    form_data = await request.form()

    # Teachers are submitted as teacher_email_1, teacher_email_2, etc.
    teachers = []
    i = 1
    while f"teacher_email_{i}" in form_data:
        email = form_data.get(f"teacher_email_{i}")
        if email:
            teachers.append({
                "email": email,
                "first_name": form_data.get(f"teacher_first_name_{i}", ""),
                "last_name": form_data.get(f"teacher_last_name_{i}", ""),
            })
        i += 1

    if teachers:
        await service.invite_teachers(db, teachers)

    return RedirectResponse(url="/onboarding/step5", status_code=302)


@router.get("/step5", response_class=HTMLResponse)
async def onboarding_step5(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Step 5: Complete."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    auth_service = get_auth_service()
    current_user = await auth_service.get_current_user(db, user_id)

    service = get_onboarding_service()
    status = await service.get_onboarding_status(db)

    return templates.TemplateResponse(
        "onboarding/step5_complete.html",
        {
            "request": request,
            "current_user": current_user,
            "status": status,
            "step": 5,
        },
    )


@router.post("/complete", response_class=HTMLResponse)
async def onboarding_complete(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mark onboarding as complete and redirect to dashboard."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    service = get_onboarding_service()
    await service.complete_onboarding(db)

    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/skip", response_class=HTMLResponse)
async def onboarding_skip(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Skip onboarding and go to dashboard."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    service = get_onboarding_service()
    await service.complete_onboarding(db)

    return RedirectResponse(url="/dashboard", status_code=302)
