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
    get_tenant_id_or_none,
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


# ISO 4217 → display symbol. Anything not in this map falls back to the
# code itself (e.g. "CHF 1,234.56") which is the safe default.
_CURRENCY_SYMBOLS = {
    "ZAR": "R",
    "USD": "$",
    "GBP": "£",
    "EUR": "€",
    "KES": "KSh",
    "NGN": "₦",
    "GHS": "GH₵",
    "BWP": "P",
    "ZMW": "K",
    "MWK": "MK",
    "NAD": "N$",
    "AUD": "A$",
    "CAD": "C$",
    "INR": "₹",
    "JPY": "¥",
    "CNY": "¥",
}


def _currency_symbol(code: str) -> str:
    return _CURRENCY_SYMBOLS.get((code or "ZAR").upper(), code or "ZAR")


async def _tenant_currency(db: AsyncSession) -> tuple[str, str]:
    """Return (currency_code, currency_symbol) for the current tenant. Falls
    back to ZAR/R so the page never blanks if settings haven't been touched."""
    from app.models import Tenant
    tenant_id = get_tenant_id_or_none()
    if not tenant_id:
        return ("ZAR", "R")
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return ("ZAR", "R")
    code = (tenant.settings or {}).get("billing_currency", "ZAR")
    return (code, _currency_symbol(code))


def _ctx(request: Request, user: User, *, currency: tuple[str, str], **extra) -> dict:
    return {
        "request": request,
        "user": user,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
        "currency_code": currency[0],
        "currency_symbol": currency[1],
        **extra,
    }


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    currency = await _tenant_currency(db)
    return templates.TemplateResponse("accounting/dashboard.html", _ctx(request, user, currency=currency))


@router.get("/expenses", response_class=HTMLResponse)
async def expenses(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    currency = await _tenant_currency(db)
    return templates.TemplateResponse("accounting/expenses.html", _ctx(request, user, currency=currency))


@router.get("/income", response_class=HTMLResponse)
async def income(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    currency = await _tenant_currency(db)
    return templates.TemplateResponse("accounting/income.html", _ctx(request, user, currency=currency))


@router.get("/vendors", response_class=HTMLResponse)
async def vendors(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    currency = await _tenant_currency(db)
    return templates.TemplateResponse("accounting/vendors.html", _ctx(request, user, currency=currency))


@router.get("/accounts", response_class=HTMLResponse)
async def accounts(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    currency = await _tenant_currency(db)
    return templates.TemplateResponse("accounting/accounts.html", _ctx(request, user, currency=currency))


@router.get("/banks", response_class=HTMLResponse)
async def banks(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    currency = await _tenant_currency(db)
    return templates.TemplateResponse("accounting/banks.html", _ctx(request, user, currency=currency))


@router.get("/reports", response_class=HTMLResponse)
async def reports(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    _require_school_admin(user)
    currency = await _tenant_currency(db)
    return templates.TemplateResponse("accounting/reports.html", _ctx(request, user, currency=currency))
