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
from app.web._parent_context import (
    SELECTED_CHILD_COOKIE,
    get_parent_child_context,
    is_own_child,
    remember_child,
    requested_child_id,
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
    """Check authentication and return redirect if not authenticated.

    Preserves the current URL as ``?next=`` on the login redirect so
    deep-linked pages (e.g. an invoice URL tapped from WhatsApp) send
    the user back to the intended page after login instead of the
    generic dashboard.
    """
    user_id = get_current_user_id_or_none()
    if not user_id:
        from urllib.parse import quote
        next_url = request.url.path
        if request.url.query:
            next_url += "?" + request.url.query
        response = RedirectResponse(
            url=f"/login?next={quote(next_url, safe='/?&=')}",
            status_code=302,
        )
        response.delete_cookie("access_token")
        return response
    return None


async def _get_tenant(db: AsyncSession):
    """Get tenant for feature flag checks."""
    from app.models import Tenant
    tenant_id = get_tenant_id()
    return await db.get(Tenant, tenant_id)


def _how_to_pay(tenant) -> dict:
    """What a parent needs to settle fees (P-BILL-01).

    Read straight from the tenant's billing settings — the same values
    the invoice email + PDF print. ClassUp never collects fees itself.
    """
    settings_ = (tenant.settings or {}) if tenant else {}
    return {
        "banking_details": (settings_.get("billing_banking_details") or "").strip() or None,
        "payment_instructions": (settings_.get("billing_payment_instructions") or "").strip() or None,
        "school_name": tenant.name if tenant else None,
        "school_phone": tenant.phone if tenant else None,
        "school_email": tenant.email if tenant else None,
    }


_PAYMENT_METHOD_LABELS = {
    "CASH": "Cash", "BANK_TRANSFER": "Bank transfer", "EFT": "EFT",
    "CARD": "Card", "CHEQUE": "Cheque", "OTHER": "Other",
}


def _plain_invoice_status(invoice, today: date) -> tuple[str, str]:
    """(css-modifier, plain words) for an invoice — no accounting terms."""
    status = (invoice.status or "").upper()
    balance = invoice.balance or 0
    if status == "PAID" or (balance <= 0 and status not in ("DRAFT", "CANCELLED")):
        return "paid", "Paid"
    if status == "CANCELLED":
        return "info", "Cancelled"
    if status == "OVERDUE" or (invoice.due_date and invoice.due_date < today):
        return "overdue", "Overdue"
    if status == "PARTIALLY_PAID":
        return "due", "Part paid"
    return "due", "To pay"


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
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"

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
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"
    # Hide DAY_SCHOLARS / BOARDERS fee-item options on tenants that
    # aren't MIXED — they'd never match any student otherwise and the
    # confusing "Set the school type in Settings → School Operations"
    # help text just makes an admin wonder what they did wrong.
    school_type = tenant.get_setting("school_type") if tenant else None
    show_residence_scopes = school_type == "MIXED"

    context = {
        "request": request,
        "user": user,
        "fee_items": fee_items,
        "classes": classes,
        "currency": currency,
        "show_residence_scopes": show_residence_scopes,
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
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"

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
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"

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
    class_id: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Invoice list page.

    `class_id` is accepted as a string + parsed manually: the filter
    form submits "class_id=" (empty) when the admin has "All classes"
    selected, and FastAPI's UUID coercer used to 422 on that → scary
    error page. Parsing here keeps the page working with any value
    the filter form sends (empty, UUID, or junk → treated as no filter).
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if get_current_user_role() not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    # Parse class_id defensively — empty or malformed → no filter.
    parsed_class_id: uuid.UUID | None = None
    if class_id:
        try:
            parsed_class_id = uuid.UUID(class_id)
        except (ValueError, TypeError):
            parsed_class_id = None

    # Likewise, status must be one of the known invoice statuses. An
    # unknown value drops to None (= all) rather than 422ing the page.
    ALLOWED_STATUSES = {"DRAFT", "SENT", "PARTIALLY_PAID", "PAID", "OVERDUE", "CANCELLED"}
    normalised_status = status if status in ALLOWED_STATUSES else None

    billing_service = get_billing_service()
    class_service = get_class_service()

    invoices, total = await billing_service.get_invoices(
        db, class_id=parsed_class_id, status=normalised_status, page=page, page_size=20
    )
    classes, _ = await class_service.get_classes(db, is_active=True, page_size=100)
    total_pages = (total + 19) // 20

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"

    context = {
        "request": request,
        "user": user,
        "invoices": invoices,
        "classes": classes,
        "current_class_id": parsed_class_id,
        "current_status": normalised_status,
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
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"

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
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"

    context = {
        "request": request,
        "user": user,
        "invoice": invoice,
        "currency": currency,
        "is_admin": role in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value),
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    if role == Role.PARENT.value:
        today = date.today()
        status_mod, status_text = _plain_invoice_status(invoice, today)
        context.update({
            "how_to_pay": _how_to_pay(tenant),
            "plain_status": status_text,
            "plain_status_mod": status_mod,
            "days_overdue": (today - invoice.due_date).days
            if invoice.due_date and invoice.due_date < today and status_mod == "overdue" else 0,
            "payment_method_labels": _PAYMENT_METHOD_LABELS,
        })
        child_ctx = await get_parent_child_context(request, db, explicit=invoice.student_id)
        context.update(child_ctx)
        response = templates.TemplateResponse("billing/invoices/detail.html", context)
        remember_child(response, child_ctx)
        return response
    return templates.TemplateResponse("billing/invoices/detail.html", context)


@router.get("/invoices/{invoice_id}/pdf")
async def invoice_pdf(
    request: Request,
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Download an invoice as PDF (P-BILL-03).

    Same access path as the invoice detail page: parents go through
    ``verify_parent_access`` + the parent-scoped ``get_invoice`` (own
    children only, never drafts → 404). Teachers have no billing access.
    The PDF is the same one the invoice email attaches, built with the
    same parameters.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    role = get_current_user_role()
    if role not in (Role.PARENT.value, Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    billing_service = get_billing_service()
    if role == Role.PARENT.value:
        from app.utils.tenant_context import get_current_user_id
        if not await billing_service.verify_parent_access(db, get_current_user_id(), invoice_id):
            raise ForbiddenException("Access denied")
    invoice = await billing_service.get_invoice(db, invoice_id)

    from fastapi.responses import Response
    from app.services import jurisdiction_service
    from app.services.invoice_pdf import generate_invoice_pdf

    tenant = await _get_tenant(db)
    jurisdiction = await jurisdiction_service.get_jurisdiction_for_tenant(db, tenant)
    htp = _how_to_pay(tenant)
    student_name = (
        f"{invoice.student.first_name} {invoice.student.last_name}"
        if invoice.student else "Student"
    )
    pdf_bytes = generate_invoice_pdf(
        invoice_number=invoice.invoice_number,
        student_name=student_name,
        due_date=invoice.due_date.strftime("%d %b %Y"),
        total_amount=f"{invoice.total_amount:.2f}",
        currency=jurisdiction.currency_symbol,
        line_items=[
            {
                "description": item.description,
                "quantity": item.quantity,
                "unit_amount": float(item.unit_amount),
                "total_amount": float(item.total_amount),
            }
            for item in (invoice.items or [])
        ],
        tenant_name=tenant.name if tenant else "School",
        tenant_address=tenant.address if tenant else None,
        tenant_phone=tenant.phone if tenant else None,
        tenant_email=tenant.email if tenant else None,
        banking_details=htp["banking_details"],
        payment_instructions=htp["payment_instructions"],
        # Stored values straight off the invoice (no recalculation).
        amount_paid=invoice.amount_paid,
        balance_due=invoice.balance,
    )
    import re
    safe_number = re.sub(r"[^A-Za-z0-9._-]+", "_", invoice.invoice_number or "invoice")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_number}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


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
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"

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


@router.get("/payments/import", response_class=HTMLResponse)
async def payments_import_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the bulk payment upload page.

    Admins download the pre-populated template (every active student with
    their oldest open invoice + current balance), fill in the amounts for
    students being paid for, and upload it back here.
    """
    redirect = _require_auth(request)
    if redirect:
        return redirect

    user = await _get_current_user(db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if get_current_user_role() not in (Role.SCHOOL_ADMIN.value, Role.SUPER_ADMIN.value):
        raise ForbiddenException("Access denied")

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"

    context = {
        "request": request,
        "user": user,
        "currency_code": currency,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
    }
    return templates.TemplateResponse("billing/payments/import.html", context)


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

    from app.utils.tenant_context import get_current_user_id
    user_id = get_current_user_id()

    tenant = await _get_tenant(db)
    currency = tenant.get_setting("billing_currency", "USD") if tenant else "USD"

    context = {
        "request": request,
        "user": user,
        "currency": currency,
        "current_language": get_current_language(),
        "permissions": PermissionChecker(user.role),
        "children_fees": [],
        "family": None,
    }
    if role != Role.PARENT.value:
        # Staff use /billing; this page is the parent "Fees" view.
        return templates.TemplateResponse("billing/statement.html", context)

    # Parent "Fees" page (P-BILL-02) — presentation over existing data:
    # stored invoice balances/statuses, the existing statement entries
    # and the existing per-child balance figures. No new calculations.
    child_ctx = await get_parent_child_context(request, db)
    children = child_ctx.get("parent_children", [])
    balances = {b.student_id: b for b in await billing_service.get_children_balances(db, user_id)}
    today = date.today()

    children_fees = []
    for child in children:
        invoices, _ = await billing_service.get_parent_invoices(
            db, user_id, page=1, page_size=200, student_id=child.id,
        )
        try:
            statement = await billing_service.get_student_statement(db, child.id)
        except Exception:
            statement = None
        to_pay, paid = [], []
        overdue_amount = 0
        for inv in invoices:
            mod, text = _plain_invoice_status(inv, today)
            row = {"invoice": inv, "status_mod": mod, "status_text": text}
            if mod == "paid":
                paid.append(row)
            elif mod != "info":
                to_pay.append(row)
                if mod == "overdue":
                    overdue_amount += inv.balance or 0
        to_pay.sort(key=lambda r: (r["invoice"].due_date or today))
        payments = [
            e for e in (statement.entries if statement else []) if e.type == "PAYMENT"
        ]
        payments.sort(key=lambda e: e.date, reverse=True)
        bal = balances.get(child.id)
        children_fees.append({
            "child": child,
            "balance": bal.balance if bal else 0,
            "total_invoiced": bal.total_invoiced if bal else 0,
            "total_paid": bal.total_paid if bal else 0,
            "to_pay": to_pay,
            "paid": paid,
            "overdue_amount": overdue_amount,
            "payments": payments,
            "statement": statement,
        })

    # Which child to open: ?child_id → remembered child → the child with
    # the largest overdue amount → first child.
    selected = child_ctx.get("selected_child")
    requested = requested_child_id(request) or request.cookies.get(SELECTED_CHILD_COOKIE)
    if children_fees and not is_own_child(child_ctx, requested):
        worst = max(children_fees, key=lambda cf: cf["overdue_amount"])
        if worst["overdue_amount"] > 0:
            selected = worst["child"]
    child_ctx = {**child_ctx, "selected_child": selected,
                 "selected_child_id": selected.id if selected else None}

    family_outstanding = sum((cf["balance"] for cf in children_fees if cf["balance"] > 0), 0)
    context.update({
        **child_ctx,
        "children_fees": children_fees,
        "selected_fees": next((cf for cf in children_fees if selected and cf["child"].id == selected.id), None),
        "family": {
            "outstanding": family_outstanding,
            "owing_children": sum(1 for cf in children_fees if cf["balance"] > 0),
            "overdue": sum((cf["overdue_amount"] for cf in children_fees), 0),
        },
        "how_to_pay": _how_to_pay(tenant),
        "payment_method_labels": _PAYMENT_METHOD_LABELS,
    })
    response = templates.TemplateResponse("billing/statement.html", context)
    remember_child(response, child_ctx)
    return response
