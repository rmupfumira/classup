"""Parent Portal iteration 1 — security + behaviour tests.

What must hold (non-negotiable):

  * The persisted child selection (``selected_child_id`` cookie) and any
    ``?child_id=`` / ``?student_id=`` value are only a *preference*.
    Another family's child id is ignored and falls back to the parent's
    own first child — it can never select or reveal someone else's child.
  * ``GET /reports/{id}/pdf`` only renders reports the viewer may see
    (parents: own children's published reports); anything else
    redirects to /reports without rendering.
  * ``GET /billing/invoices/{id}/pdf`` is denied for another family's
    invoice and for draft invoices; works for the parent's own sent
    invoice and returns a PDF attachment.
  * Parent deep links keep ``?next=`` on the login redirect.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.exceptions import ForbiddenException, NotFoundException
from app.models import (
    BillingInvoice,
    BillingInvoiceItem,
    DailyReport,
    ParentStudent,
    SchoolClass,
    Student,
    Tenant,
    User,
)
from app.models.report import ReportTemplate
from app.models.user import Role
from app.utils.security import hash_password
from app.utils.tenant_context import _current_user_id, _current_user_role, _tenant_id
from app.web import _parent_context as pc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mk_user(tenant_id, first, last, role=Role.PARENT.value):
    return User(
        id=uuid.uuid4(), tenant_id=tenant_id,
        email=f"{first.lower()}-{uuid.uuid4().hex[:8]}@iter1.test",
        password_hash=hash_password("x"),
        first_name=first, last_name=last, role=role, is_active=True,
    )


@pytest_asyncio.fixture
async def families(db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, test_admin: User):
    """Family A (two children, deliberately inserted out of name order)
    and family B (one child) in the same tenant, plus reports and
    invoices for each."""
    test_tenant.settings = {
        **(test_tenant.settings or {}),
        "features": {**((test_tenant.settings or {}).get("features") or {}), "billing": True},
        "billing_currency": "USD",
        "billing_banking_details": "Test Bank\nAcc 123",
    }
    parent_a = _mk_user(test_tenant.id, "Alice", "Family")
    parent_b = _mk_user(test_tenant.id, "Bob", "Other")
    zed = Student(id=uuid.uuid4(), tenant_id=test_tenant.id, first_name="Zed", last_name="Family",
                  class_id=test_class.id, is_active=True)
    amy = Student(id=uuid.uuid4(), tenant_id=test_tenant.id, first_name="Amy", last_name="Family",
                  class_id=test_class.id, is_active=True)
    other = Student(id=uuid.uuid4(), tenant_id=test_tenant.id, first_name="Olly", last_name="Other",
                    class_id=test_class.id, is_active=True)
    db.add_all([parent_a, parent_b, zed, amy, other])
    await db.flush()
    db.add_all([
        ParentStudent(id=uuid.uuid4(), parent_id=parent_a.id, student_id=zed.id, relationship_type="PARENT", is_primary=True),
        ParentStudent(id=uuid.uuid4(), parent_id=parent_a.id, student_id=amy.id, relationship_type="PARENT", is_primary=True),
        ParentStudent(id=uuid.uuid4(), parent_id=parent_b.id, student_id=other.id, relationship_type="PARENT", is_primary=True),
    ])

    template = ReportTemplate(
        id=uuid.uuid4(), tenant_id=test_tenant.id, name="Term Report",
        report_type="REPORT_CARD", frequency="TERMLY", sections=[], is_active=True,
    )
    db.add(template)
    await db.flush()
    now = datetime.now(timezone.utc)

    def _report(student, status):
        return DailyReport(
            id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=student.id,
            class_id=test_class.id, template_id=template.id,
            report_date=date.today() - timedelta(days=3 if status == "FINALIZED" else 2),
            report_data={"sections": {}}, status=status,
            finalized_at=now if status == "FINALIZED" else None,
            created_by=test_admin.id,
        )
    rep_amy = _report(amy, "FINALIZED")
    rep_amy_draft = _report(amy, "DRAFT")
    rep_other = _report(other, "FINALIZED")
    db.add_all([rep_amy, rep_amy_draft, rep_other])

    def _invoice(student, number, status):
        inv = BillingInvoice(
            id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=student.id,
            invoice_number=number,
            billing_period_start=date.today() - timedelta(days=30),
            billing_period_end=date.today(),
            due_date=date.today() - timedelta(days=5),
            subtotal=Decimal("100.00"), total_amount=Decimal("100.00"),
            amount_paid=Decimal("0.00"), balance=Decimal("100.00"),
            status=status, created_by=test_admin.id,
        )
        return inv
    inv_amy = _invoice(amy, f"INV-T-{uuid.uuid4().hex[:4]}", "OVERDUE")
    inv_amy_draft = _invoice(amy, f"INV-T-{uuid.uuid4().hex[:4]}", "DRAFT")
    inv_other = _invoice(other, f"INV-T-{uuid.uuid4().hex[:4]}", "SENT")
    db.add_all([inv_amy, inv_amy_draft, inv_other])
    await db.flush()
    db.add(BillingInvoiceItem(
        id=uuid.uuid4(), invoice_id=inv_amy.id, description="Tuition",
        quantity=1, unit_amount=Decimal("100.00"), total_amount=Decimal("100.00"),
    ))
    await db.commit()
    return {
        "tenant": test_tenant, "parent_a": parent_a, "parent_b": parent_b,
        "amy": amy, "zed": zed, "other": other,
        "rep_amy": rep_amy, "rep_amy_draft": rep_amy_draft, "rep_other": rep_other,
        "inv_amy": inv_amy, "inv_amy_draft": inv_amy_draft, "inv_other": inv_other,
    }


@pytest.fixture
def as_parent_a(families):
    tok_t = _tenant_id.set(families["tenant"].id)
    tok_r = _current_user_role.set(Role.PARENT.value)
    tok_u = _current_user_id.set(families["parent_a"].id)
    try:
        yield families
    finally:
        _current_user_id.reset(tok_u)
        _current_user_role.reset(tok_r)
        _tenant_id.reset(tok_t)


def _request(path: str, *, query: str = "", cookies: dict | None = None, tenant=None) -> Request:
    cookie_header = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
    headers = [(b"host", b"testserver")]
    if cookie_header:
        headers.append((b"cookie", cookie_header.encode()))
    from app.main import app as asgi_app  # templates call url_for()

    scope = {
        "app": asgi_app,
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "headers": headers, "scheme": "http",
        "server": ("testserver", 80), "client": ("127.0.0.1", 1234), "root_path": "",
        "state": {"tenant": tenant} if tenant is not None else {},
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# Selection helper: validation + canonical order
# ---------------------------------------------------------------------------

class TestChildSelection:
    async def test_canonical_order_is_by_first_name(self, db: AsyncSession, as_parent_a):
        fam = as_parent_a
        kids = await pc.load_parent_children(db, fam["parent_a"].id)
        assert [k.first_name for k in kids] == ["Amy", "Zed"]

    async def test_foreign_child_in_cookie_is_ignored(self, db: AsyncSession, as_parent_a):
        fam = as_parent_a
        req = _request("/dashboard", cookies={pc.SELECTED_CHILD_COOKIE: str(fam["other"].id)})
        ctx = await pc.get_parent_child_context(req, db)
        assert ctx["selected_child"].id == fam["amy"].id  # first own child
        assert fam["other"].id not in {c.id for c in ctx["parent_children"]}

    async def test_foreign_child_in_query_is_ignored(self, db: AsyncSession, as_parent_a):
        fam = as_parent_a
        req = _request("/reports", query=f"child_id={fam['other'].id}")
        ctx = await pc.get_parent_child_context(req, db)
        assert ctx["selected_child"].id == fam["amy"].id
        req2 = _request("/reports", query=f"student_id={fam['other'].id}")
        ctx2 = await pc.get_parent_child_context(req2, db)
        assert ctx2["selected_child"].id == fam["amy"].id

    async def test_own_child_in_cookie_is_honoured(self, db: AsyncSession, as_parent_a):
        fam = as_parent_a
        req = _request("/dashboard", cookies={pc.SELECTED_CHILD_COOKIE: str(fam["zed"].id)})
        ctx = await pc.get_parent_child_context(req, db)
        assert ctx["selected_child"].id == fam["zed"].id

    async def test_malformed_values_fall_back(self, db: AsyncSession, as_parent_a):
        fam = as_parent_a
        req = _request("/dashboard", query="child_id=not-a-uuid", cookies={pc.SELECTED_CHILD_COOKIE: "<script>"})
        ctx = await pc.get_parent_child_context(req, db)
        assert ctx["selected_child"].id == fam["amy"].id

    async def test_non_parent_gets_no_child_context(self, db: AsyncSession, families):
        tok_t = _tenant_id.set(families["tenant"].id)
        tok_r = _current_user_role.set(Role.SCHOOL_ADMIN.value)
        tok_u = _current_user_id.set(uuid.uuid4())
        try:
            ctx = await pc.get_parent_child_context(_request("/reports"), db)
            assert ctx == {}
        finally:
            _current_user_id.reset(tok_u); _current_user_role.reset(tok_r); _tenant_id.reset(tok_t)

    async def test_select_child_route_never_stores_foreign_id(self, db: AsyncSession, as_parent_a):
        from app.web.dashboard import select_child
        fam = as_parent_a
        resp = await select_child(
            _request("/dashboard/select-child"), child_id=str(fam["other"].id),
            next="/billing/statement", db=db,
        )
        assert resp.status_code == 302
        cookie = resp.headers.get("set-cookie", "")
        assert str(fam["other"].id) not in cookie
        assert str(fam["amy"].id) in cookie

    async def test_select_child_rejects_offsite_next(self, db: AsyncSession, as_parent_a):
        from app.web.dashboard import select_child
        fam = as_parent_a
        for bad in ("https://evil.test/x", "//evil.test", "/\\evil.test"):
            resp = await select_child(_request("/dashboard/select-child"), child_id=str(fam["zed"].id), next=bad, db=db)
            assert resp.headers["location"].startswith("/dashboard")

    async def test_select_child_swaps_child_id_in_path(self, db: AsyncSession, as_parent_a):
        from app.web.dashboard import select_child
        fam = as_parent_a
        resp = await select_child(
            _request("/dashboard/select-child"), child_id=str(fam["zed"].id),
            next=f"/attendance/student/{fam['amy'].id}", db=db,
        )
        assert resp.headers["location"] == f"/attendance/student/{fam['zed'].id}"


# ---------------------------------------------------------------------------
# Parent reports list: foreign student_id never widens access
# ---------------------------------------------------------------------------

class TestParentReportsList:
    async def test_foreign_student_id_shows_only_own_child(self, db: AsyncSession, as_parent_a):
        from app.web.reports import reports_list
        fam = as_parent_a
        req = _request("/reports", query=f"student_id={fam['other'].id}", tenant=fam["tenant"])
        resp = await reports_list(req, class_id=None, student_id=fam["other"].id, report_date=None,
                                  status=None, page=1, db=db)
        ids = {r["id"] for r in resp.context["reports"]}
        assert fam["rep_other"].id not in ids
        assert fam["rep_amy"].id in ids          # own child's published report
        assert fam["rep_amy_draft"].id not in ids  # drafts never shown to parents
        # D5: no calendar for this tenant's country/year → month label,
        # never a guessed term.
        label = resp.context["term_labels"][fam["rep_amy"].id]
        assert label == fam["rep_amy"].report_date.strftime("%B %Y")


# ---------------------------------------------------------------------------
# Report PDF route
# ---------------------------------------------------------------------------

class TestReportPdf:
    async def test_own_published_report_downloads(self, db: AsyncSession, as_parent_a, monkeypatch):
        from app.services import report_pdf
        from app.web.reports import report_pdf as report_pdf_route
        calls = []

        async def fake_render(_db, report):
            calls.append(report.id)
            return b"%PDF-1.4 fake"
        monkeypatch.setattr(report_pdf, "render_report_pdf", fake_render)
        fam = as_parent_a
        resp = await report_pdf_route(_request(f"/reports/{fam['rep_amy'].id}/pdf"), fam["rep_amy"].id, db)
        assert resp.status_code == 200
        assert resp.media_type == "application/pdf"
        assert resp.headers["content-disposition"].startswith("attachment;")
        assert calls == [fam["rep_amy"].id]

    @pytest.mark.parametrize("key", ["rep_other", "rep_amy_draft"])
    async def test_out_of_scope_report_redirects_without_rendering(self, db: AsyncSession, as_parent_a, monkeypatch, key):
        from app.services import report_pdf
        from app.web.reports import report_pdf as report_pdf_route

        async def boom(*_a, **_k):  # must never be reached
            raise AssertionError("rendered an out-of-scope report")
        monkeypatch.setattr(report_pdf, "render_report_pdf", boom)
        fam = as_parent_a
        resp = await report_pdf_route(_request(f"/reports/{fam[key].id}/pdf"), fam[key].id, db)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/reports"


# ---------------------------------------------------------------------------
# Invoice PDF route
# ---------------------------------------------------------------------------

class TestInvoicePdf:
    async def test_own_invoice_downloads_as_pdf(self, db: AsyncSession, as_parent_a):
        from app.web.billing import invoice_pdf
        fam = as_parent_a
        resp = await invoice_pdf(_request(f"/billing/invoices/{fam['inv_amy'].id}/pdf"), fam["inv_amy"].id, db)
        assert resp.status_code == 200
        assert resp.media_type == "application/pdf"
        assert resp.body.startswith(b"%PDF")
        assert fam["inv_amy"].invoice_number in resp.headers["content-disposition"]

    @pytest.mark.parametrize("key", ["inv_other", "inv_amy_draft"])
    async def test_foreign_or_draft_invoice_denied(self, db: AsyncSession, as_parent_a, monkeypatch, key):
        from app.services import invoice_pdf as invoice_pdf_mod
        from app.web.billing import invoice_pdf

        def boom(**_k):
            raise AssertionError("generated a PDF the parent may not see")
        monkeypatch.setattr(invoice_pdf_mod, "generate_invoice_pdf", boom)
        fam = as_parent_a
        with pytest.raises((NotFoundException, ForbiddenException)):
            await invoice_pdf(_request(f"/billing/invoices/{fam[key].id}/pdf"), fam[key].id, db)

    async def test_teacher_denied(self, db: AsyncSession, families):
        from app.web.billing import invoice_pdf
        tok_t = _tenant_id.set(families["tenant"].id)
        tok_r = _current_user_role.set(Role.TEACHER.value)
        tok_u = _current_user_id.set(uuid.uuid4())
        try:
            with pytest.raises(ForbiddenException):
                await invoice_pdf(_request("/x"), families["inv_amy"].id, db)
        finally:
            _current_user_id.reset(tok_u); _current_user_role.reset(tok_r); _tenant_id.reset(tok_t)


# ---------------------------------------------------------------------------
# Fees page: opens on own child, never another family's
# ---------------------------------------------------------------------------

class TestFeesPage:
    async def test_foreign_cookie_falls_back_and_hides_other_family(self, db: AsyncSession, as_parent_a):
        from app.web.billing import statement_page
        fam = as_parent_a
        req = _request("/billing/statement", cookies={pc.SELECTED_CHILD_COOKIE: str(fam["other"].id)}, tenant=fam["tenant"])
        resp = await statement_page(req, db)
        ctx = resp.context
        child_ids = {cf["child"].id for cf in ctx["children_fees"]}
        assert child_ids == {fam["amy"].id, fam["zed"].id}
        # No valid preference → the child with the largest overdue balance.
        assert ctx["selected_child"].id == fam["amy"].id
        invoice_ids = {row["invoice"].id for cf in ctx["children_fees"] for row in cf["to_pay"] + cf["paid"]}
        assert fam["inv_other"].id not in invoice_ids
        assert fam["inv_amy_draft"].id not in invoice_ids


# ---------------------------------------------------------------------------
# Deep links keep ?next= (P-COMM-02)
# ---------------------------------------------------------------------------

class TestNextPreserved:
    def test_login_redirect_helper(self):
        resp = pc.login_redirect(_request("/reports/abc", query="child_id=1"))
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login?next=/reports/abc%3Fchild_id%3D1"

    @pytest.mark.parametrize("path", [
        "/reports",
        f"/reports/{uuid.uuid4()}",
        f"/attendance/student/{uuid.uuid4()}",
        "/attendance/my-child",
        "/events",
        "/announcements",
        "/messages",
        f"/students/{uuid.uuid4()}",
        f"/timetable/child/{uuid.uuid4()}",
        f"/billing/invoices/{uuid.uuid4()}",
        "/billing/statement",
    ])
    async def test_unauthenticated_deep_link_keeps_next(self, path):
        from urllib.parse import parse_qs, urlsplit
        from app.main import app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get(path, follow_redirects=False)
        assert resp.status_code in (302, 303, 307), resp.status_code
        loc = urlsplit(resp.headers["location"])
        assert loc.path == "/login"
        assert parse_qs(loc.query)["next"] == [path]

    def test_safe_next_path(self):
        assert pc.safe_next_path("/fees") == "/fees"
        assert pc.safe_next_path("//evil") == "/dashboard"
        assert pc.safe_next_path("https://evil") == "/dashboard"
        assert pc.safe_next_path(None) == "/dashboard"


# ---------------------------------------------------------------------------
# Money format (P-BILL-05)
# ---------------------------------------------------------------------------

class TestMoney:
    def test_money_format(self):
        assert pc.money(Decimal("2300"), "USD") == "$2,300.00"
        assert pc.money(Decimal("-500"), "USD") == "-$500.00"
        assert pc.money(1234.5, "ZAR") == "R1,234.50"
        assert pc.money(None, "USD") == "$0.00"


# ---------------------------------------------------------------------------
# Dashboard aggregator additions (P-DASH-01/03/04, P-MULTI-03)
# ---------------------------------------------------------------------------

class TestDashboardAdditions:
    async def test_invoice_cards_are_labelled_and_collapsed(self, db: AsyncSession, families, test_admin: User):
        from app.services.parent_dashboard_service import build_parent_dashboard
        fam = families
        # Second overdue invoice for Amy → one collapsed card for Amy.
        db.add(BillingInvoice(
            id=uuid.uuid4(), tenant_id=fam["tenant"].id, student_id=fam["amy"].id,
            invoice_number=f"INV-T-{uuid.uuid4().hex[:4]}",
            billing_period_start=date.today() - timedelta(days=90),
            billing_period_end=date.today() - timedelta(days=60),
            due_date=date.today() - timedelta(days=40),
            subtotal=Decimal("50.00"), total_amount=Decimal("50.00"),
            amount_paid=Decimal("0.00"), balance=Decimal("50.00"),
            status="OVERDUE", created_by=test_admin.id,
        ))
        await db.commit()
        pd = await build_parent_dashboard(db, fam["parent_a"].id, fam["tenant"].id)
        invoices = [i for i in pd.needs_attention if i.kind == "invoice"]
        assert len(invoices) == 1
        card = invoices[0]
        assert card.child_label == "Amy"
        assert "2 overdue invoices" in card.title and "$150.00" in card.title
        assert card.primary_url == f"/billing/statement?child_id={fam['amy'].id}"
        # Family total (2+ children) — sum of stored balances, own family only.
        assert pd.family_outstanding == Decimal("150.00")
        assert pd.family_outstanding_text == "$150.00"

    async def test_no_emoji_and_today_status(self, db: AsyncSession, families):
        from app.services.parent_dashboard_service import build_parent_dashboard
        fam = families
        pd = await build_parent_dashboard(db, fam["parent_a"].id, fam["tenant"].id)
        assert pd.today_status is not None
        assert pd.today_status.state in ("unmarked", "no_school")
        for item in pd.this_week + pd.needs_attention:
            assert "✓" not in item.title and "📄" not in item.title

    async def test_late_is_never_a_tick(self, db: AsyncSession, families, test_class: SchoolClass, test_admin: User):
        from app.models import AttendanceRecord
        from app.services.parent_dashboard_service import build_parent_dashboard
        fam = families
        db.add(AttendanceRecord(
            id=uuid.uuid4(), tenant_id=fam["tenant"].id, student_id=fam["amy"].id,
            class_id=test_class.id, date=date.today(), status="LATE",
            check_in_time=datetime.now(timezone.utc).replace(hour=8, minute=12),
            recorded_by=test_admin.id,
        ))
        await db.commit()
        pd = await build_parent_dashboard(db, fam["parent_a"].id, fam["tenant"].id, fam["amy"].id)
        assert pd.today_status.state == "late"
        assert pd.today_status.label.startswith("Late")
        today_dot = [d for d in pd.week_dots if d.day == date.today()]
        if today_dot:
            assert today_dot[0].state == "late"

    async def test_rsvps_scoped_to_family_and_include_class_events(self, db: AsyncSession, families, test_class: SchoolClass, test_admin: User):
        from app.models import SchoolEvent
        from app.services.parent_dashboard_service import build_parent_dashboard
        fam = families
        now = datetime.now(timezone.utc)

        def _ev(title, scope, **kw):
            return SchoolEvent(
                id=uuid.uuid4(), tenant_id=fam["tenant"].id, created_by=test_admin.id,
                title=title, event_type="OTHER", scope=scope, starts_at=now + timedelta(days=3),
                timezone="UTC", rsvp_required=True, rsvp_deadline=now + timedelta(days=2), **kw,
            )
        db.add_all([
            _ev("Class trip", "CLASS", class_id=test_class.id),
            _ev("Other family meeting", "STUDENT", student_id=fam["other"].id),
            _ev("Amy meeting", "STUDENT", student_id=fam["amy"].id),
        ])
        await db.commit()
        pd = await build_parent_dashboard(db, fam["parent_a"].id, fam["tenant"].id)
        titles = {i.title: i for i in pd.needs_attention if i.kind == "event_rsvp"}
        assert "Class trip" in titles
        assert "Amy meeting" in titles and titles["Amy meeting"].child_label == "Amy"
        assert "Other family meeting" not in titles
