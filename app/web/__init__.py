"""Web router aggregator for HTML pages."""

from fastapi import APIRouter, Depends

from app.utils.permissions import require_feature
from app.web import (
    academic,
    announcements,
    attendance,
    auth,
    billing,
    classes,
    dashboard,
    documents,
    help as help_web,
    imports,
    invitations,
    messages,
    onboarding,
    photos,
    reports,
    settings,
    students,
    subscription,
    super_admin,
    teachers,
    tenant_slug,
    timetable,
)

web_router = APIRouter(include_in_schema=False)

# Core pages (always available)
web_router.include_router(auth.router)
web_router.include_router(dashboard.router)
web_router.include_router(students.router)
web_router.include_router(classes.router)
web_router.include_router(announcements.router)
web_router.include_router(attendance.router)
web_router.include_router(reports.router)
web_router.include_router(onboarding.router)
web_router.include_router(settings.router)
web_router.include_router(imports.router)
web_router.include_router(invitations.router)
web_router.include_router(messages.router)
web_router.include_router(teachers.router)
web_router.include_router(subscription.router)
web_router.include_router(super_admin.router)
web_router.include_router(help_web.router)

# Plan-gated pages — redirect to /subscription?locked=X when feature is off
web_router.include_router(
    billing.router,
    dependencies=[Depends(require_feature("billing"))],
)
web_router.include_router(
    photos.router,
    dependencies=[Depends(require_feature("photo_sharing"))],
)
web_router.include_router(
    documents.router,
    dependencies=[Depends(require_feature("document_sharing"))],
)
web_router.include_router(
    academic.router,
    dependencies=[Depends(require_feature("subject_management"))],
)
web_router.include_router(
    timetable.router,
    dependencies=[Depends(require_feature("timetable_management"))],
)

# Must be registered LAST — /{slug} is a catch-all that matches any
# top-level path not claimed by another route. Keep it at the bottom.
web_router.include_router(tenant_slug.router)
