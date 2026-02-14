"""API v1 router aggregator."""

from fastapi import APIRouter

from app.api.v1 import (
    academic,
    admin,
    attendance,
    auth,
    classes,
    files,
    imports,
    invitations,
    reports,
    students,
    webhooks,
    websocket,
    whatsapp,
)

api_router = APIRouter(tags=["API v1"])

# Include all API routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(students.router, prefix="/students", tags=["Students"])
api_router.include_router(classes.router, prefix="/classes", tags=["Classes"])
api_router.include_router(attendance.router, prefix="/attendance", tags=["Attendance"])
api_router.include_router(files.router, prefix="/files", tags=["Files"])
api_router.include_router(reports.router, prefix="/reports", tags=["Reports"])
api_router.include_router(invitations.router, prefix="/invitations", tags=["Invitations"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
api_router.include_router(imports.router, prefix="/imports", tags=["Imports"])
api_router.include_router(academic.router, prefix="/academic", tags=["Academic"])
api_router.include_router(admin.router)
api_router.include_router(websocket.router)
api_router.include_router(whatsapp.router)
