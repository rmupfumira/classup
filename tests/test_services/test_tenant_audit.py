"""Tenant-scoped audit viewer tests.

The new SCHOOL_ADMIN audit endpoint must:
  - Only return events for the caller's tenant (no cross-tenant leaks).
  - Respect the action_prefix filter so the UI can show "Billing only".
  - Paginate the same shape as the super-admin viewer.

We exercise the service directly (list_events) with a tenant_id so the
underlying filter is the one the API relies on.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant
from app.models.audit import AuditLog
from app.services.audit_service import get_audit_service


@pytest_asyncio.fixture
async def seeded_events(db: AsyncSession, test_tenant: Tenant):
    """Three events on our tenant + one event on a different tenant.
    The second tenant has to be a real row — audit_logs has an FK to it.
    """
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other School",
        slug=f"other-{uuid.uuid4().hex[:6]}",
        email="other@example.com",
        settings={},
        is_active=True,
    )
    db.add(other_tenant)
    await db.flush()

    now = datetime.now(timezone.utc)
    rows = [
        AuditLog(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
            action="billing.payments.create",
            method="POST",
            path="/api/v1/billing/payments",
            status_code=200,
            created_at=now,
        ),
        AuditLog(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
            action="billing.invoices.create",
            method="POST",
            path="/api/v1/billing/invoices",
            status_code=200,
            created_at=now - timedelta(minutes=5),
        ),
        AuditLog(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
            action="students.create",
            method="POST",
            path="/api/v1/students",
            status_code=200,
            created_at=now - timedelta(minutes=10),
        ),
        AuditLog(
            id=uuid.uuid4(),
            tenant_id=other_tenant.id,
            tenant_name=other_tenant.name,
            action="billing.payments.create",
            method="POST",
            path="/api/v1/billing/payments",
            status_code=200,
            created_at=now,
        ),
    ]
    for r in rows:
        db.add(r)
    await db.commit()
    return rows


class TestTenantScopedAudit:
    async def test_scopes_to_caller_tenant(
        self, db: AsyncSession, test_tenant: Tenant, seeded_events,
    ):
        """A tenant_id filter returns ONLY that tenant's rows."""
        svc = get_audit_service()
        items, total = await svc.list_events(
            db, tenant_id=test_tenant.id, page_size=20,
        )
        assert total == 3
        assert all(e.tenant_id == test_tenant.id for e in items)

    async def test_action_prefix_filter(
        self, db: AsyncSession, test_tenant: Tenant, seeded_events,
    ):
        """action='billing.' should pull the two billing rows and skip
        the students.create one."""
        svc = get_audit_service()
        items, total = await svc.list_events(
            db, tenant_id=test_tenant.id, action="billing.", page_size=20,
        )
        assert total == 2
        assert {e.action for e in items} == {
            "billing.payments.create",
            "billing.invoices.create",
        }

    async def test_payments_only_filter(
        self, db: AsyncSession, test_tenant: Tenant, seeded_events,
    ):
        svc = get_audit_service()
        items, total = await svc.list_events(
            db, tenant_id=test_tenant.id, action="billing.payments", page_size=20,
        )
        assert total == 1
        assert items[0].action == "billing.payments.create"
