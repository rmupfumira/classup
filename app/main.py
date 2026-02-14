"""FastAPI application factory and entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import close_db
from app.exceptions import (
    ClassUpException,
    ValidationException,
    create_exception_handlers,
)
from app.templates_config import templates

# Configure logging - force DEBUG level for development
log_level = logging.DEBUG if settings.is_development else getattr(logging, settings.app_log_level)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,  # Override any existing configuration
)
# Set all app loggers to DEBUG
logging.getLogger("app").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)
logger.info(f"Logging configured at level: {logging.getLevelName(log_level)}")

# Base paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan events."""
        logger.info(f"Starting {settings.app_name} in {settings.app_env} mode")
        yield
        logger.info(f"Shutting down {settings.app_name}")
        await close_db()

    app = FastAPI(
        title=settings.app_name,
        description="Multi-tenant SaaS platform for managing schools and daycare centers",
        version="2.0.0",
        docs_url="/api/docs" if settings.app_debug else None,
        redoc_url="/api/redoc" if settings.app_debug else None,
        openapi_url="/api/openapi.json" if settings.app_debug else None,
        lifespan=lifespan,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.is_development else [settings.app_base_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add authentication middleware
    from app.middleware.auth import AuthMiddleware
    app.add_middleware(AuthMiddleware)

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Register exception handlers
    exception_handlers = create_exception_handlers(templates)
    for exc_class, handler in exception_handlers.items():
        app.add_exception_handler(exc_class, handler)

    # Register routers
    register_routers(app)

    return app


def register_routers(app: FastAPI):
    """Register all API and web routers."""
    from app.api.v1 import api_router
    from app.web import web_router

    # API routes (versioned)
    app.include_router(api_router, prefix="/api/v1")

    # Web routes (HTML pages)
    app.include_router(web_router)

    # Health check endpoint
    @app.get("/health", tags=["Health"])
    async def health_check():
        """Health check endpoint for load balancers and monitoring."""
        return {"status": "healthy", "app": settings.app_name, "env": settings.app_env}

    # Root redirect
    @app.get("/", include_in_schema=False)
    async def root(request: Request):
        """Redirect root to login or dashboard based on auth status."""
        from fastapi.responses import RedirectResponse
        from app.utils.security import decode_access_token

        # Check if user is authenticated with a valid token
        token = request.cookies.get("access_token")
        if token:
            payload = decode_access_token(token)
            if payload:
                return RedirectResponse(url="/dashboard", status_code=302)
            # Invalid token - clear it and go to login
            response = RedirectResponse(url="/login", status_code=302)
            response.delete_cookie("access_token")
            return response
        return RedirectResponse(url="/login", status_code=302)


# Create the app instance
app = create_app()


def main():
    """Entry point for running the application."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.is_development,
        log_level=settings.app_log_level.lower(),
    )


if __name__ == "__main__":
    main()
