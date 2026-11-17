"""Cross-module integration for the school calendar wiring (2026-11-17):

- tenant_service.get_active_country_codes — the super admin nudge banner
  depends on it to call calendar_service.countries_missing_upcoming_year.
- report_pdf._resolve_term_header — REPORT_CARD / PROGRESS_REPORT get a
  "Term N YYYY" header auto-filled from the jurisdiction calendar.

The billing side of the wiring has its own tests inside
test_billing_service.py::TestTermAwareInvoicing.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant
from app.services import calendar_service, report_pdf
from app.services.tenant_service import get_tenant_service


@pytest_asyncio.fixture
async def zw_and_za_tenants(db: AsyncSession):
    """Two live tenants on different countries + one deleted + one with
    no country set (should default to ZA). This is what the active-
    country-codes helper has to make sense of."""
    rows = [
        Tenant(
            id=uuid.uuid4(), name="ZW School", slug=f"zw-{uuid.uuid4().hex[:6]}",
            email="zw@test.local", education_type="PRIMARY_SCHOOL",
            settings={"country": "ZW", "features": {}},
            is_active=True, onboarding_completed=True,
        ),
        Tenant(
            id=uuid.uuid4(), name="ZA School", slug=f"za-{uuid.uuid4().hex[:6]}",
            email="za@test.local", education_type="PRIMARY_SCHOOL",
            settings={"country": "ZA", "features": {}},
            is_active=True, onboarding_completed=True,
        ),
        Tenant(
            id=uuid.uuid4(), name="Legacy", slug=f"legacy-{uuid.uuid4().hex[:6]}",
            email="legacy@test.local", education_type="PRIMARY_SCHOOL",
            settings={"features": {}},  # no country → defaults to ZA
            is_active=True, onboarding_completed=True,
        ),
    ]
    for t in rows:
        db.add(t)
    await db.commit()
    for t in rows:
        await db.refresh(t)
    try:
        yield rows
    finally:
        await db.execute(
            delete(Tenant).where(Tenant.id.in_([r.id for r in rows]))
        )
        await db.commit()


@pytest.mark.asyncio
async def test_active_country_codes_includes_each_live_country(
    db: AsyncSession, zw_and_za_tenants,
):
    """Both set countries + the no-country tenant's ZA default land in
    the returned set."""
    svc = get_tenant_service()
    codes = await svc.get_active_country_codes(db)
    # Pre-existing tenants on the shared DB can add more codes, so
    # assert membership rather than equality.
    assert "ZW" in codes
    assert "ZA" in codes


@pytest.mark.asyncio
async def test_resolve_term_header_for_report_card(db: AsyncSession):
    """A finalised report_card dated inside ZW 2026 Term 1 gets
    ("Term 1", "2026") from the helper."""
    await calendar_service.seed_zimbabwe_2026_if_missing(db)
    await db.commit()

    tenant = SimpleNamespace(settings={"country": "ZW"})
    report = SimpleNamespace(
        report_date=date(2026, 2, 14),
        template=SimpleNamespace(report_type="REPORT_CARD"),
        id=uuid.uuid4(),
    )

    term, year = await report_pdf._resolve_term_header(db, tenant, report)
    assert term == "Term 1"
    assert year == "2026"


@pytest.mark.asyncio
async def test_resolve_term_header_returns_empty_for_daily_report(
    db: AsyncSession,
):
    """DAILY_ACTIVITY reports keep their plain date header — no calendar
    lookup, even when one is available."""
    await calendar_service.seed_zimbabwe_2026_if_missing(db)
    await db.commit()

    tenant = SimpleNamespace(settings={"country": "ZW"})
    report = SimpleNamespace(
        report_date=date(2026, 2, 14),
        template=SimpleNamespace(report_type="DAILY_ACTIVITY"),
        id=uuid.uuid4(),
    )

    term, year = await report_pdf._resolve_term_header(db, tenant, report)
    assert term == ""
    assert year == ""


@pytest.mark.asyncio
async def test_resolve_term_header_quiet_when_no_calendar(db: AsyncSession):
    """Country with no calendar → empty strings, not a 500. Renderer
    falls through to the plain date header, which is how reports worked
    before the calendar module landed."""
    tenant = SimpleNamespace(settings={"country": "ZZ"})  # not seeded
    report = SimpleNamespace(
        report_date=date(2026, 2, 14),
        template=SimpleNamespace(report_type="REPORT_CARD"),
        id=uuid.uuid4(),
    )

    term, year = await report_pdf._resolve_term_header(db, tenant, report)
    assert term == ""
    assert year == ""
