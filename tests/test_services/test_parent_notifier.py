"""Tests for the parent notifier — the shim that mirrors email
notifications to WhatsApp for opted-in parents.

Two things matter here:

  1. The **gate** — which combinations of opt-in / tenant flag / plan
     flag / service configuration actually let a notification through.
     A false-positive silently spams a parent who never asked for
     WhatsApp; a false-negative silently drops a notification they
     did opt into. Both are user-visible bugs, both are tested.

  2. **Best-effort semantics** — WhatsApp failure must never
     propagate. A silent False return is fine; a raised exception is
     a regression (the caller's email would look broken from the log).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant, User
from app.models.user import Role
from app.services import parent_notifier
from app.utils.security import hash_password


# ---------------------------------------------------------------------------
# Fixture — a parent + tenant with WhatsApp fully wired
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def opted_in(db: AsyncSession):
    """A parent who's ready to receive WhatsApp notifications:
    active, has a phone, opted in, on a tenant with whatsapp_enabled
    on both plan and tenant flags."""
    tenant_id = uuid.uuid4()
    slug = f"pn-{tenant_id.hex[:8]}"
    tenant = Tenant(
        id=tenant_id, name=f"Notif Test {tenant_id.hex[:6]}", slug=slug,
        email=f"admin@{slug}.test", education_type="PRIMARY_SCHOOL",
        settings={"features": {"whatsapp_enabled": True}},
        is_active=True, onboarding_completed=True,
    )
    db.add(tenant)
    await db.flush()

    parent = User(
        id=uuid.uuid4(), tenant_id=tenant_id,
        email=f"parent-{tenant_id.hex[:6]}@t.test",
        password_hash=hash_password("x"),
        first_name="Nomsa", last_name="Khumalo",
        role=Role.PARENT.value, is_active=True,
        whatsapp_phone="+27722621278", whatsapp_opted_in=True,
    )
    db.add(parent)
    await db.commit()

    try:
        yield SimpleNamespace(tenant=tenant, parent=parent, tenant_id=tenant_id)
    finally:
        from app.database import get_db_context
        from sqlalchemy import text as sql_text
        async with get_db_context() as db2:
            await db2.execute(
                sql_text("DELETE FROM tenants WHERE id = :tid"),
                {"tid": tenant_id},
            )
            await db2.commit()


def _plan_features(**flags):
    """Build a subscription-service mock that returns a plan with the
    given feature flags."""
    plan = SimpleNamespace(features=flags, name="Test Plan")
    sub = SimpleNamespace(plan=plan)
    svc = SimpleNamespace(get_tenant_subscription=AsyncMock(return_value=sub))
    return svc


def _configured_wa_service():
    """WhatsApp service mock — configured + every send helper is an
    AsyncMock returning a dict (mimics a successful Meta 200)."""
    svc = MagicMock()
    svc.is_configured = True
    for method in [
        "send_attendance_alert", "send_report_ready", "send_announcement",
        "send_parent_invite", "send_welcome",
    ]:
        setattr(svc, method, AsyncMock(return_value={"messages": [{"id": "wa_1"}]}))
    return svc


# ---------------------------------------------------------------------------
# Gate tests — every rejection reason
# ---------------------------------------------------------------------------

class TestGate:
    async def test_full_stack_ready_returns_true(
        self, db: AsyncSession, opted_in
    ):
        with patch(
            "app.services.subscription_service.get_subscription_service",
            return_value=_plan_features(whatsapp_enabled=True),
        ), patch(
            "app.services.parent_notifier.get_whatsapp_service_from_db",
            new=AsyncMock(return_value=_configured_wa_service()),
        ):
            ok = await parent_notifier._can_notify_whatsapp(db, opted_in.parent)
        assert ok is True

    async def test_no_user_returns_false(self, db: AsyncSession):
        assert await parent_notifier._can_notify_whatsapp(db, None) is False

    async def test_no_phone_returns_false(
        self, db: AsyncSession, opted_in
    ):
        opted_in.parent.whatsapp_phone = None
        await db.commit()
        assert await parent_notifier._can_notify_whatsapp(db, opted_in.parent) is False

    async def test_not_opted_in_returns_false(
        self, db: AsyncSession, opted_in
    ):
        opted_in.parent.whatsapp_opted_in = False
        await db.commit()
        assert await parent_notifier._can_notify_whatsapp(db, opted_in.parent) is False

    async def test_tenant_feature_off_returns_false(
        self, db: AsyncSession, opted_in
    ):
        opted_in.tenant.settings = {"features": {"whatsapp_enabled": False}}
        await db.commit()
        with patch(
            "app.services.subscription_service.get_subscription_service",
            return_value=_plan_features(whatsapp_enabled=True),
        ):
            ok = await parent_notifier._can_notify_whatsapp(db, opted_in.parent)
        assert ok is False

    async def test_plan_feature_off_returns_false(
        self, db: AsyncSession, opted_in
    ):
        with patch(
            "app.services.subscription_service.get_subscription_service",
            return_value=_plan_features(whatsapp_enabled=False),
        ):
            ok = await parent_notifier._can_notify_whatsapp(db, opted_in.parent)
        assert ok is False

    async def test_service_not_configured_returns_false(
        self, db: AsyncSession, opted_in
    ):
        svc = MagicMock()
        svc.is_configured = False
        with patch(
            "app.services.subscription_service.get_subscription_service",
            return_value=_plan_features(whatsapp_enabled=True),
        ), patch(
            "app.services.parent_notifier.get_whatsapp_service_from_db",
            new=AsyncMock(return_value=svc),
        ):
            ok = await parent_notifier._can_notify_whatsapp(db, opted_in.parent)
        assert ok is False


# ---------------------------------------------------------------------------
# Dispatcher tests — each event calls the right WhatsApp template
# ---------------------------------------------------------------------------

class TestDispatcher:
    """Each notify_X function should call the right service method with
    the right positional args. For events without a bespoke template
    (invoice_sent, invoice_overdue, etc.), the fallback is
    send_announcement with a well-formed subject."""

    async def _wired(self, db: AsyncSession, opted_in):
        """Set up patches + return the mock service so tests can assert
        on what was called."""
        svc = _configured_wa_service()
        ctx1 = patch(
            "app.services.subscription_service.get_subscription_service",
            return_value=_plan_features(whatsapp_enabled=True),
        )
        ctx2 = patch(
            "app.services.parent_notifier.get_whatsapp_service_from_db",
            new=AsyncMock(return_value=svc),
        )
        ctx1.start()
        ctx2.start()
        return svc, [ctx1, ctx2]

    def _stop(self, patches):
        for p in patches:
            p.stop()

    async def test_attendance_uses_bespoke_template(
        self, db: AsyncSession, opted_in
    ):
        svc, patches = await self._wired(db, opted_in)
        try:
            ok = await parent_notifier.notify_attendance_alert(
                db, opted_in.parent,
                student_name="Sipho", status="ABSENT", tenant_name="Acme School",
            )
        finally:
            self._stop(patches)
        assert ok is True
        svc.send_attendance_alert.assert_awaited_once()
        call = svc.send_attendance_alert.await_args
        assert call.args == ("+27722621278", "Sipho", "ABSENT", "Acme School")

    async def test_report_ready_uses_bespoke_template(
        self, db: AsyncSession, opted_in
    ):
        svc, patches = await self._wired(db, opted_in)
        try:
            await parent_notifier.notify_report_ready(
                db, opted_in.parent,
                report_type="Progress Report",
                student_name="Sipho",
                url="https://classup.co.za/reports/r1",
            )
        finally:
            self._stop(patches)
        svc.send_report_ready.assert_awaited_once()

    async def test_invoice_sent_falls_back_to_announcement(
        self, db: AsyncSession, opted_in
    ):
        """No bespoke template for invoice_sent yet — must use the
        generic announcement template with a subject line that carries
        the important info (number, amount, due date)."""
        svc, patches = await self._wired(db, opted_in)
        try:
            await parent_notifier.notify_invoice_sent(
                db, opted_in.parent,
                tenant_name="Acme", student_name="Sipho",
                invoice_number="INV-2026-0001",
                total_amount=Decimal("500.00"),
                due_date_str="15 Oct 2026",
            )
        finally:
            self._stop(patches)
        svc.send_announcement.assert_awaited_once()
        # send_announcement(to_phone, school_name, subject)
        call = svc.send_announcement.await_args
        assert call.args[1] == "Acme"
        subject = call.args[2]
        # The subject line must carry the invoice number, amount, and due date
        # so the parent gets the same signal from WhatsApp as from email.
        assert "INV-2026-0001" in subject
        assert "500" in subject
        assert "15 Oct 2026" in subject
        # And the bespoke templates for other events were NOT touched.
        svc.send_attendance_alert.assert_not_awaited()

    async def test_photo_shared_falls_back_to_announcement(
        self, db: AsyncSession, opted_in
    ):
        svc, patches = await self._wired(db, opted_in)
        try:
            await parent_notifier.notify_photo_shared(
                db, opted_in.parent,
                tenant_name="Acme", sharer_name="Ms Grace",
                class_name="Grade 3A", photo_count=4,
            )
        finally:
            self._stop(patches)
        svc.send_announcement.assert_awaited_once()
        subject = svc.send_announcement.await_args.args[2]
        assert "4" in subject and "Grade 3A" in subject

    async def test_payment_received_partial_balance(
        self, db: AsyncSession, opted_in
    ):
        """When a payment leaves a positive remaining balance, the
        WhatsApp subject line must include both the paid amount AND
        the remaining balance — parents scan it in 2 seconds and need
        to know where they stand."""
        svc, patches = await self._wired(db, opted_in)
        try:
            await parent_notifier.notify_payment_received(
                db, opted_in.parent,
                tenant_name="Acme", student_name="Sipho",
                invoice_number="INV-2026-0001",
                payment_amount=Decimal("200.00"),
                remaining_balance=Decimal("300.00"),
            )
        finally:
            self._stop(patches)
        subject = svc.send_announcement.await_args.args[2]
        assert "200" in subject
        assert "300" in subject
        assert "INV-2026-0001" in subject
        assert "Sipho" in subject

    async def test_payment_received_fully_paid_says_thank_you(
        self, db: AsyncSession, opted_in
    ):
        """Zero balance → "Fully paid up — thank you!" instead of
        "Remaining balance R0" (which would read as debt-shaming a
        parent who just settled up)."""
        svc, patches = await self._wired(db, opted_in)
        try:
            await parent_notifier.notify_payment_received(
                db, opted_in.parent,
                tenant_name="Acme", student_name="Sipho",
                invoice_number="INV-2026-0001",
                payment_amount=Decimal("500.00"),
                remaining_balance=Decimal("0.00"),
            )
        finally:
            self._stop(patches)
        subject = svc.send_announcement.await_args.args[2]
        assert "Fully paid" in subject
        assert "thank you" in subject.lower()

    async def test_not_opted_in_returns_false_no_send(
        self, db: AsyncSession, opted_in
    ):
        """Gate rejects → no send happens. The email side sees a clean
        False return, not an exception."""
        opted_in.parent.whatsapp_opted_in = False
        await db.commit()

        svc, patches = await self._wired(db, opted_in)
        try:
            ok = await parent_notifier.notify_attendance_alert(
                db, opted_in.parent,
                student_name="Sipho", status="ABSENT", tenant_name="Acme",
            )
        finally:
            self._stop(patches)
        assert ok is False
        svc.send_attendance_alert.assert_not_awaited()


# ---------------------------------------------------------------------------
# Best-effort — WhatsApp errors must never propagate
# ---------------------------------------------------------------------------

class TestBestEffort:
    async def test_send_failure_returns_false_no_raise(
        self, db: AsyncSession, opted_in
    ):
        """If Meta 500s or the network drops, the caller should see a
        False return, not an exception that would poison the email flow."""
        svc = _configured_wa_service()
        svc.send_attendance_alert = AsyncMock(
            side_effect=RuntimeError("Meta had a bad day"),
        )
        with patch(
            "app.services.subscription_service.get_subscription_service",
            return_value=_plan_features(whatsapp_enabled=True),
        ), patch(
            "app.services.parent_notifier.get_whatsapp_service_from_db",
            new=AsyncMock(return_value=svc),
        ):
            # No pytest.raises — this must NOT propagate.
            ok = await parent_notifier.notify_attendance_alert(
                db, opted_in.parent,
                student_name="Sipho", status="ABSENT", tenant_name="Acme",
            )
        assert ok is False
