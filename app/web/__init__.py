"""Web router aggregator for HTML pages."""

from fastapi import APIRouter

from app.web import (
    attendance,
    auth,
    classes,
    dashboard,
    documents,
    imports,
    messages,
    onboarding,
    photos,
    reports,
    settings,
    students,
)

web_router = APIRouter(include_in_schema=False)

# Include all web routers
web_router.include_router(auth.router)
web_router.include_router(dashboard.router)
web_router.include_router(students.router)
web_router.include_router(classes.router)
web_router.include_router(attendance.router)
web_router.include_router(messages.router)
web_router.include_router(photos.router)
web_router.include_router(documents.router)
web_router.include_router(reports.router)
web_router.include_router(onboarding.router)
web_router.include_router(settings.router)
web_router.include_router(imports.router)
