"""API v1 router aggregator."""

from fastapi import APIRouter

from app.api.v1 import auth, classes, students

api_router = APIRouter(tags=["API v1"])

# Include all API routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(students.router, prefix="/students", tags=["Students"])
api_router.include_router(classes.router, prefix="/classes", tags=["Classes"])
