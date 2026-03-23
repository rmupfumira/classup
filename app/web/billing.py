"""Billing web routes for HTML pages."""

import csv
import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.models.user import Role, User
from app.services.auth_service import get_auth_service
from app.services.billing_service import get_billing_service
from app.services.class_service import get_class_service
from app.services.student_service import get_student_service
from app.templates_config import templates
from app.utils.permissions import PermissionChecker
from app.utils.tenant_context import (
    get_current_language,
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id,
)

router = APIRouter(prefix="/billing")


async def _get_current_user(db: AsyncSession) -> User | None:
    """Get the current user from the database."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        return None
    auth_service = get_auth_service()
    try:
        return await auth_service.get_current_user(db, user_id)
    except Exception:
        return None


def _require_auth(request: Request):
    """Check authentication and return redirect if not authenticated."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie("access_token")
        return response
    return None


async def _get_tenant(db: AsyncSession):
    """Get tenant for feature flag checks."""
    from app.models import Tenant
    tenant_id = get_tenant_id()
    return await db.get(Tenant, tenant_id)


# =========================================================================
# Admin Routes
# =========================================================================

@router.get("", response_class=HTMLResponse)
async def billing_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Billing dashboard with summary stats."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()

    # Parents go to statement view
    if role == Role.PARENT.value:
        return RedirectResponse(url="/billing/statement", status_code=302)

    if role not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("You don't have permission to access billing")

    billing_service = get_billing_service()

    # Check overdue invoices & send reminders in an isolated session
    # so any failure doesn't poison the main dashboard query
    from app.database import get_db_context
    try:
        async with get_db_context() as side_db:
            await billing_service.check_overdue_invoices(side_db)
            await billing_service.send_overdue_reminders(side_db)
    except Exception:
        pass  # Non-critical background tasks, don't block dashboard

    summary = await billing_service.get_billing_summary(db)

    # Recent invoices
    recent_invoices, _ = await billing_service.get_invoices(db, page=1, page_size=5)

    # Recent payments
    recent_payments, _ = await billing_service.get_payments(db, page=1, page_size=5)

    # Get tenant for currency
    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "ZAR") if tenant else "ZAR"

    context = {
        "request": request,
        "user": user,
        "summary": summary,
        "recent_invoices": recent_invoices,
        "recent_payments": recent_payments,
        "currency": currency,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("billing/dashboard.html", context)


@router.get("/fee-items", response_class=HTMLResponse)
async def fee_items_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Fee items management page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if get_current_user_role() not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    billing_service = get_billing_service()
    class_service = get_class_service()

    fee_items, total = await billing_service.get_fee_items(db, page_size=100)
    classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "ZAR") if tenant else "ZAR"

    context = {
        "request": request,
        "user": user,
        "fee_items": fee_items,
        "classes": classes,
        "currency": currency,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("billing/fee_items.html", context)


@router.get("/arrears", response_class=HTMLResponse)
async def arrears_report(
    request: Request,
    class_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Arrears / ageing report page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if get_current_user_role() not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    billing_service = get_billing_service()
    class_service = get_class_service()

    arrears_data = await billing_service.get_arrears_report(db, class_id=class_id)
    classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "ZAR") if tenant else "ZAR"

    # Totals
    from decimal import Decimal
    totals = {
        "total_due": sum((d["total_due"] for d in arrears_data), Decimal("0")),
        "total_paid": sum((d["total_paid"] for d in arrears_data), Decimal("0")),
        "outstanding": sum((d["outstanding"] for d in arrears_data), Decimal("0")),
        "current": sum((d["current"] for d in arrears_data), Decimal("0")),
        "days_30": sum((d["days_30"] for d in arrears_data), Decimal("0")),
        "days_60": sum((d["days_60"] for d in arrears_data), Decimal("0")),
        "days_90_plus": sum((d["days_90_plus"] for d in arrears_data), Decimal("0")),
    }

    context = {
        "request": request,
        "user": user,
        "arrears_data": arrears_data,
        "classes": classes,
        "current_class_id": class_id,
        "currency": currency,
        "totals": totals,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("billing/arrears.html", context)


@router.get("/arrears/export/csv")
async def arrears_export_csv(
    request: Request,
    class_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Export arrears report to CSV."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if get_current_user_role() not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    billing_service = get_billing_service()
    arrears_data = await billing_service.get_arrears_report(db, class_id=class_id)

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "ZAR") if tenant else "ZAR"

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student", "Class", "Total Due", "Total Paid", "Outstanding", "Current", "30 Days", "60 Days", "90+ Days"])
    for d in arrears_data:
        s = d["student"]
        class_name = s.school_class.name if s and s.school_class else ""
        writer.writerow([
            f"{s.first_name} {s.last_name}" if s else "",
            class_name,
            f"{d['total_due']:.2f}",
            f"{d['total_paid']:.2f}",
            f"{d['outstanding']:.2f}",
            f"{d['current']:.2f}",
            f"{d['days_30']:.2f}",
            f"{d['days_60']:.2f}",
            f"{d['days_90_plus']:.2f}",
        ])

    output.seek(0)
    today = date.today().isoformat()
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=arrears_report_{today}.csv"},
    )


@router.get("/invoices", response_class=HTMLResponse)
async def invoices_list(
    request: Request,
    class_id: uuid.UUID | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Invoice list page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if get_current_user_role() not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    billing_service = get_billing_service()
    class_service = get_class_service()

    invoices, total = await billing_service.get_invoices(
        db, class_id=class_id, status=status, page=page, page_size=20
    )
    classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)
    total_pages = (total + 19) // 20

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "ZAR") if tenant else "ZAR"

    context = {
        "request": request,
        "user": user,
        "invoices": invoices,
        "classes": classes,
        "current_class_id": class_id,
        "current_status": status,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "currency": currency,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("billing/invoices/list.html", context)


@router.get("/invoices/generate", response_class=HTMLResponse)
async def generate_invoices_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Batch invoice generation wizard."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if get_current_user_role() not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    billing_service = get_billing_service()
    class_service = get_class_service()

    fee_items, _ = await billing_service.get_fee_items(db, is_active=True, page_size=100)
    classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "ZAR") if tenant else "ZAR"

    context = {
        "request": request,
        "user": user,
        "fee_items": fee_items,
        "classes": classes,
        "currency": currency,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("billing/invoices/generate.html", context)


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
async def invoice_detail(
    request: Request,
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Invoice detail page (admin: full view + record payment, parent: read-only)."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    billing_service = get_billing_service()

    # Parent access check
    if role == Role.PARENT.value:
        from app.utils.tenant_context import get_current_user_id
        parent_id = get_current_user_id()
        has_access = await billing_service.verify_parent_access(db, parent_id, invoice_id)
        if not has_access:
            raise ForbiddenException("Access denied")

    invoice = await billing_service.get_invoice(db, invoice_id)

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "ZAR") if tenant else "ZAR"

    context = {
        "request": request,
        "user": user,
        "invoice": invoice,
        "currency": currency,
        "is_admin": role in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value),
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("billing/invoices/detail.html", context)


@router.get("/payments", response_class=HTMLResponse)
async def payments_list(
    request: Request,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Payment list page."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if get_current_user_role() not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    billing_service = get_billing_service()
    payments, total = await billing_service.get_payments(db, page=page, page_size=20)
    total_pages = (total + 19) // 20

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "ZAR") if tenant else "ZAR"

    context = {
        "request": request,
        "user": user,
        "payments": payments,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "currency": currency,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("billing/payments/list.html", context)


# =========================================================================
# Parent Routes
# =========================================================================

@router.get("/statement", response_class=HTMLResponse)
async def statement_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Parent statement view with tabs per child."""
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    role = get_current_user_role()
    if role not in (Role.PARENT.value, Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    billing_service = get_billing_service()
    student_service = get_student_service()

    from app.utils.tenant_context import get_current_user_id
    user_id = get_current_user_id()

    if role == Role.PARENT.value:
        children = await student_service.get_my_children(db, user_id)
    else:
        # Admin viewing — show all students (or could be filtered by query param)
        children = []

    # Build statement per child
    children_statements = []
    for child in children:
        try:
            statement = await billing_service.get_student_statement(db, child.id)
            children_statements.append({
                "child": child,
                "statement": statement,
            })
        except Exception:
            children_statements.append({
                "child": child,
                "statement": None,
            })

    # Get balances
    balances = await billing_service.get_children_balances(db, user_id)

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "ZAR") if tenant else "ZAR"

    context = {
        "request": request,
        "user": user,
        "children_statements": children_statements,
        "balances": balances,
        "currency": currency,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("billing/statement.html", context)
