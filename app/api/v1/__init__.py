"""API v1 router aggregator."""

from fastapi import APIRouter, Depends

from app.api.v1 import (
    academic,
    admin,
    announcements,
    attendance,
    audit,
    auth,
    billing,
    classes,
    documents,
    files,
    grade_levels,
    imports,
    invitations,
    messages,
    photos,
    reports,
    students,
    subscriptions,
    timetable,
    users,
    webhooks,
    websocket,
    whatsapp,
)
from app.utils.permissions import require_feature

api_router = APIRouter(tags=["API v1"])

# Core routers (always available)
api_router.include_router(announcements.router, prefix="/announcements", tags=["Announcements"])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(students.router, prefix="/students", tags=["Students"])
api_router.include_router(classes.router, prefix="/classes", tags=["Classes"])
api_router.include_router(attendance.router, prefix="/attendance", tags=["Attendance"])
api_router.include_router(files.router, prefix="/files", tags=["Files"])
api_router.include_router(reports.router, prefix="/reports", tags=["Reports"])
api_router.include_router(invitations.router, prefix="/invitations", tags=["Invitations"])
api_router.include_router(messages.router, prefix="/messages", tags=["Messages"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
api_router.include_router(imports.router, prefix="/imports", tags=["Imports"])
api_router.include_router(grade_levels.router, prefix="/grade-levels", tags=["Grade Levels"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(subscriptions.router)
api_router.include_router(admin.router)
api_router.include_router(audit.router)
api_router.include_router(websocket.router)
api_router.include_router(whatsapp.router)  # Public webhook inside — individual /send endpoint gated below

# Plan-gated routers — blocked (402) if the tenant's plan doesn't include the feature
api_router.include_router(
    billing.router,
    prefix="/billing",
    tags=["Billing"],
    dependencies=[Depends(require_feature("billing"))],
)
api_router.include_router(
    documents.router,
    prefix="/documents",
    tags=["Documents"],
    dependencies=[Depends(require_feature("document_sharing"))],
)
api_router.include_router(
    photos.router,
    prefix="/photos",
    tags=["Photos"],
    dependencies=[Depends(require_feature("photo_sharing"))],
)
api_router.include_router(
    academic.router,
    prefix="/academic",
    tags=["Academic"],
    dependencies=[Depends(require_feature("subject_management"))],
)
api_router.include_router(
    timetable.router,
    prefix="/timetable",
    tags=["Timetable"],
    dependencies=[Depends(require_feature("timetable_management"))],
)
