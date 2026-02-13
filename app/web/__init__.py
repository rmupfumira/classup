"""Web router aggregator for HTML pages."""

from fastapi import APIRouter

from app.web import auth, classes, dashboard, students

web_router = APIRouter(include_in_schema=False)

# Include all web routers
web_router.include_router(auth.router)
web_router.include_router(dashboard.router)
web_router.include_router(students.router)
web_router.include_router(classes.router)
