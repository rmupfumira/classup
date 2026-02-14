"""Custom exception classes and global exception handlers."""

import logging
import traceback

from fastapi import Request
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.templating import Jinja2Templates

logger = logging.getLogger(__name__)


class ClassUpException(Exception):
    """Base exception for all ClassUp-specific errors."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class NotFoundException(ClassUpException):
    """Resource not found exception."""

    def __init__(self, resource: str = "Resource"):
        super().__init__(f"{resource} not found", 404)


class ForbiddenException(ClassUpException):
    """Access forbidden exception."""

    def __init__(self, message: str = "You don't have permission to perform this action"):
        super().__init__(message, 403)


class UnauthorizedException(ClassUpException):
    """Authentication required exception."""

    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, 401)


class ConflictException(ClassUpException):
    """Resource conflict exception."""

    def __init__(self, message: str = "Resource already exists"):
        super().__init__(message, 409)


class ValidationException(ClassUpException):
    """Validation error exception with field-level errors."""

    def __init__(self, errors: list[dict] | str):
        if isinstance(errors, str):
            errors = [{"field": "general", "message": errors}]
        super().__init__("Validation failed", 422)
        self.errors = errors


class TenantContextError(ClassUpException):
    """Tenant context not set error."""

    def __init__(self, message: str = "Tenant context is required"):
        super().__init__(message, 400)


class UserContextError(ClassUpException):
    """User context not set error."""

    def __init__(self, message: str = "User context is required"):
        super().__init__(message, 401)


def wants_json(request: Request) -> bool:
    """Check if request expects JSON (API) or HTML (web)."""
    return (
        request.url.path.startswith("/api/")
        or "application/json" in request.headers.get("accept", "")
        or request.headers.get("content-type", "").startswith("application/json")
    )


def create_exception_handlers(templates: Jinja2Templates):
    """Create exception handlers that use the provided templates."""

    async def classup_exception_handler(request: Request, exc: ClassUpException):
        """Handle ClassUp custom exceptions."""
        logger.warning(f"ClassUpException on {request.method} {request.url.path}: {exc.message} (status={exc.status_code})")

        if wants_json(request):
            content = {
                "status": "error",
                "message": exc.message,
            }
            if hasattr(exc, "errors"):
                content["errors"] = exc.errors
            return JSONResponse(status_code=exc.status_code, content=content)

        # HTML error page
        try:
            return templates.TemplateResponse(
                f"errors/{exc.status_code}.html",
                {"request": request, "message": exc.message},
                status_code=exc.status_code,
            )
        except Exception:
            # Fallback to generic error template
            return templates.TemplateResponse(
                "errors/generic.html",
                {
                    "request": request,
                    "message": exc.message,
                    "status_code": exc.status_code,
                },
                status_code=exc.status_code,
            )

    async def validation_exception_handler(request: Request, exc: ValidationException):
        """Handle validation exceptions with field-level errors."""
        logger.warning(f"ValidationException on {request.method} {request.url.path}: {exc.errors}")

        if wants_json(request):
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "status": "error",
                    "message": exc.message,
                    "errors": exc.errors,
                },
            )

        # For HTML, use generic error template with validation errors
        try:
            return templates.TemplateResponse(
                "errors/422.html",
                {"request": request, "message": exc.message, "errors": exc.errors},
                status_code=exc.status_code,
            )
        except Exception:
            # Fallback to generic error template
            return templates.TemplateResponse(
                "errors/generic.html",
                {
                    "request": request,
                    "message": exc.message,
                    "status_code": exc.status_code,
                },
                status_code=exc.status_code,
            )

    async def generic_exception_handler(request: Request, exc: Exception):
        """Handle unexpected exceptions."""
        # Log the full exception with traceback
        logger.error(f"Unhandled exception on {request.method} {request.url.path}")
        logger.error(f"Exception: {type(exc).__name__}: {exc}")
        # Use format_exception to get traceback from the exception object
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        logger.error("".join(tb_lines))

        if wants_json(request):
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "An unexpected error occurred",
                },
            )

        try:
            return templates.TemplateResponse(
                "errors/500.html",
                {"request": request, "message": "An unexpected error occurred"},
                status_code=500,
            )
        except Exception:
            return HTMLResponse(
                content="<h1>500 Internal Server Error</h1><p>An unexpected error occurred.</p>",
                status_code=500,
            )

    return {
        ClassUpException: classup_exception_handler,
        ValidationException: validation_exception_handler,
        Exception: generic_exception_handler,
    }
