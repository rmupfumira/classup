"""Authentication web routes for HTML pages."""

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.auth_service import get_auth_service
from app.templates_config import templates
from app.utils.tenant_context import get_current_language

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None, next: str | None = None):
    """Render the login page."""
    from app.utils.security import decode_access_token

    # Check if already authenticated with a valid token
    token = request.cookies.get("access_token")
    if token:
        payload = decode_access_token(token)
        if payload:
            return RedirectResponse(url="/dashboard", status_code=302)
        # Invalid token - clear the cookie
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie("access_token")
        return response

    return templates.TemplateResponse(
        "auth/login.html",
        {
            "request": request,
            "error": error,
            "next": next or "/dashboard",
            "current_language": get_current_language(),
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    remember_me: bool = Form(False),
    next: str = Form("/dashboard"),
    db: AsyncSession = Depends(get_db),
):
    """Handle login form submission."""
    try:
        auth_service = get_auth_service()
        login_request = LoginRequest(email=email, password=password, remember_me=remember_me)
        login_response, user = await auth_service.login(db, login_request)

        # Create redirect response
        redirect = RedirectResponse(url=next, status_code=302)

        # Set cookie
        cookie_max_age = settings.jwt_access_token_expire_minutes * 60
        if remember_me:
            cookie_max_age = settings.jwt_refresh_token_expire_days * 24 * 60 * 60

        redirect.set_cookie(
            key="access_token",
            value=login_response.access_token,
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
            max_age=cookie_max_age,
        )

        return redirect

    except Exception as e:
        # Re-render login page with error
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": str(e.message) if hasattr(e, "message") else "Invalid email or password",
                "email": email,
                "next": next,
                "current_language": get_current_language(),
            },
            status_code=400,
        )


@router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    code: str | None = None,
    error: str | None = None,
):
    """Render the registration page."""
    from app.utils.security import decode_access_token

    # Check if already authenticated with a valid token
    token = request.cookies.get("access_token")
    if token:
        payload = decode_access_token(token)
        if payload:
            return RedirectResponse(url="/dashboard", status_code=302)
        # Invalid token - clear the cookie
        response = RedirectResponse(url="/register", status_code=302)
        response.delete_cookie("access_token")
        return response

    return templates.TemplateResponse(
        "auth/register.html",
        {
            "request": request,
            "code": code,
            "error": error,
            "current_language": get_current_language(),
        },
    )


@router.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    invitation_code: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    phone: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Handle registration form submission."""
    try:
        auth_service = get_auth_service()
        register_request = RegisterRequest(
            invitation_code=invitation_code,
            email=email,
            password=password,
            confirm_password=confirm_password,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
        )
        await auth_service.register_parent(db, register_request)

        # Redirect to login with success message
        return RedirectResponse(
            url="/login?registered=true",
            status_code=302,
        )

    except Exception as e:
        # Re-render registration page with error
        return templates.TemplateResponse(
            "auth/register.html",
            {
                "request": request,
                "error": str(e.message) if hasattr(e, "message") else "Registration failed",
                "code": invitation_code,
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
                "current_language": get_current_language(),
            },
            status_code=400,
        )


@router.get("/logout")
async def logout(response: Response):
    """Log out the current user."""
    redirect = RedirectResponse(url="/login", status_code=302)
    redirect.delete_cookie(
        key="access_token",
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )
    return redirect


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    """Render the forgot password page."""
    return templates.TemplateResponse(
        "auth/forgot_password.html",
        {
            "request": request,
            "current_language": get_current_language(),
        },
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str | None = None):
    """Render the password reset page."""
    if not token:
        return RedirectResponse(url="/forgot-password", status_code=302)

    return templates.TemplateResponse(
        "auth/reset_password.html",
        {
            "request": request,
            "token": token,
            "current_language": get_current_language(),
        },
    )
