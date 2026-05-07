"""Accounting web routes — server-rendered pages for the school's
accounting module. The pages themselves call the JSON API for data."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
)

router = APIRouter(prefix="/accounting", tags=["accounting"])


async def _current_user(db: AsyncSession) -> User | None:
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    try:
        return await get_auth_service().get_current_user(db, user_id)
    except Exception:
        return None


def _require_school_admin(user: User | None) -> None:
    if not user or user.role not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")


def _ctx(request: Request, user: User, **extra) -> dict:
    return {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
        **extra,
    }


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    return templates.TemplateResponse("accounting/dashboard.html", _ctx(request, user))


@router.get("/expenses", response_class=HTMLResponse)
async def expenses(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    return templates.TemplateResponse("accounting/expenses.html", _ctx(request, user))


@router.get("/income", response_class=HTMLResponse)
async def income(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    return templates.TemplateResponse("accounting/income.html", _ctx(request, user))


@router.get("/vendors", response_class=HTMLResponse)
async def vendors(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    return templates.TemplateResponse("accounting/vendors.html", _ctx(request, user))


@router.get("/accounts", response_class=HTMLResponse)
async def accounts(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    return templates.TemplateResponse("accounting/accounts.html", _ctx(request, user))


@router.get("/banks", response_class=HTMLResponse)
async def banks(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    return templates.TemplateResponse("accounting/banks.html", _ctx(request, user))


@router.get("/reports", response_class=HTMLResponse)
async def reports(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    return templates.TemplateResponse("accounting/reports.html", _ctx(request, user))
