"""Per-student / hybrid subscription billing (task #159, 2026-10-12).

Covers the ChargeModel math and the student-count snapshot:

A. ``compute_cycle_amount`` — pure function, no DB:
    - FLAT plans ignore per_student_amount.
    - PER_STUDENT plans ignore the base price entirely.
    - HYBRID = base + (per_student × count).
    - Annual billing uses price_annually (or 12 × monthly fallback).
    - NULL plan / 0 students / negative count all return sane values.

B. ``count_active_students`` — DB query excludes soft-deleted
   AND is_active=False students; charges reflect the ACTIVE roster.

C. ``preview_cycle_amount`` — one dict for the UI, shaped identically
   across all three charge models.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Student, Tenant
from app.models.subscription import ChargeModel, SubscriptionPlan
from app.services.subscription_service import get_subscription_service


class TestComputeCycleAmount:
    """Pure math — no DB, no fixtures, every edge case."""

    svc = get_subscription_service()

    def test_flat_monthly(self):
        plan = SubscriptionPlan(
            price_monthly=Decimal("20.00"),
            price_annually=Decimal("216.00"),
            charge_model=ChargeModel.FLAT.value,
            per_student_amount=None,
        )
        # Per-student count ignored for FLAT.
        assert self.svc.compute_cycle_amount(plan, "MONTHLY", 45) == Decimal("20.00")
        assert self.svc.compute_cycle_amount(plan, "MONTHLY", 0) == Decimal("20.00")

    def test_flat_annual_uses_annual_price(self):
        plan = SubscriptionPlan(
            price_monthly=Decimal("20.00"),
            price_annually=Decimal("216.00"),
            charge_model=ChargeModel.FLAT.value,
        )
        assert self.svc.compute_cycle_amount(plan, "ANNUALLY", 45) == Decimal("216.00")

    def test_flat_annual_falls_back_to_twelve_times_monthly(self):
        """Admin didn't set an annual price — the cycle amount is
        12 × monthly. Keeps annual available even on plans the admin
        hasn't discounted."""
        plan = SubscriptionPlan(
            price_monthly=Decimal("20.00"),
            price_annually=None,
            charge_model=ChargeModel.FLAT.value,
        )
        assert self.svc.compute_cycle_amount(plan, "ANNUALLY", 10) == Decimal("240.00")

    def test_per_student_monthly(self):
        plan = SubscriptionPlan(
            price_monthly=Decimal("99.99"),  # ignored for PER_STUDENT
            charge_model=ChargeModel.PER_STUDENT.value,
            per_student_amount=Decimal("1.00"),
        )
        assert self.svc.compute_cycle_amount(plan, "MONTHLY", 45) == Decimal("45.00")

    def test_per_student_zero_students(self):
        plan = SubscriptionPlan(
            price_monthly=Decimal("50.00"),
            charge_model=ChargeModel.PER_STUDENT.value,
            per_student_amount=Decimal("1.00"),
        )
        # No students → nothing charged. The base is deliberately
        # ignored by PER_STUDENT so a tenant with no enrolled kids
        # pays nothing on that cycle.
        assert self.svc.compute_cycle_amount(plan, "MONTHLY", 0) == Decimal("0.00")

    def test_per_student_null_rate_treated_as_zero(self):
        """Misconfigured plan — admin forgot per_student_amount.
        We don't crash, we charge 0 (an admin-facing warning on the
        plan form would catch this before it hits a cycle)."""
        plan = SubscriptionPlan(
            price_monthly=Decimal("50.00"),
            charge_model=ChargeModel.PER_STUDENT.value,
            per_student_amount=None,
        )
        assert self.svc.compute_cycle_amount(plan, "MONTHLY", 100) == Decimal("0.00")

    def test_hybrid_monthly(self):
        plan = SubscriptionPlan(
            price_monthly=Decimal("20.00"),
            charge_model=ChargeModel.HYBRID.value,
            per_student_amount=Decimal("1.00"),
        )
        # 20 base + 45 × 1 = 65.
        assert self.svc.compute_cycle_amount(plan, "MONTHLY", 45) == Decimal("65.00")

    def test_hybrid_zero_students_charges_base_only(self):
        plan = SubscriptionPlan(
            price_monthly=Decimal("20.00"),
            charge_model=ChargeModel.HYBRID.value,
            per_student_amount=Decimal("1.00"),
        )
        assert self.svc.compute_cycle_amount(plan, "MONTHLY", 0) == Decimal("20.00")

    def test_hybrid_annual_combines_annual_base_and_per_student(self):
        plan = SubscriptionPlan(
            price_monthly=Decimal("20.00"),
            price_annually=Decimal("216.00"),
            charge_model=ChargeModel.HYBRID.value,
            per_student_amount=Decimal("12.00"),  # per student per annual cycle
        )
        # 216 base + 10 × 12 = 336.
        assert self.svc.compute_cycle_amount(plan, "ANNUALLY", 10) == Decimal("336.00")

    def test_none_plan_returns_zero(self):
        assert self.svc.compute_cycle_amount(None, "MONTHLY", 10) == Decimal("0.00")

    def test_negative_count_clamped_to_zero(self):
        """Can't happen from the DB counter, but a bad caller
        passing -5 shouldn't produce a negative bill."""
        plan = SubscriptionPlan(
            price_monthly=Decimal("20.00"),
            charge_model=ChargeModel.HYBRID.value,
            per_student_amount=Decimal("1.00"),
        )
        assert self.svc.compute_cycle_amount(plan, "MONTHLY", -5) == Decimal("20.00")

    def test_unknown_charge_model_falls_back_to_flat(self):
        """A stray / mistyped value is safer as FLAT (which only
        looks at base price) than as PER_STUDENT (which could
        suddenly bill a huge amount)."""
        plan = SubscriptionPlan(
            price_monthly=Decimal("20.00"),
            charge_model="SOMETHING_WEIRD",
            per_student_amount=Decimal("1.00"),
        )
        assert self.svc.compute_cycle_amount(plan, "MONTHLY", 45) == Decimal("20.00")


@pytest_asyncio.fixture
async def tenant_with_students(db: AsyncSession):
    """A tenant + 3 active + 1 inactive + 1 soft-deleted student."""
    from datetime import datetime, timezone

    tid = uuid.uuid4()
    tenant = Tenant(
        id=tid, name=f"Count {tid.hex[:6]}", slug=f"count-{tid.hex[:8]}",
        email=f"admin@{tid.hex[:6]}.example.com",
        education_type="PRIMARY_SCHOOL",
        settings={},
        is_active=True, onboarding_completed=True,
    )
    db.add(tenant)
    await db.flush()

    for i in range(3):
        db.add(Student(
            id=uuid.uuid4(), tenant_id=tid,
            first_name=f"Active{i}", last_name="Student",
            is_active=True,
        ))
    # Inactive — not counted (admin disabled them, no charge).
    db.add(Student(
        id=uuid.uuid4(), tenant_id=tid,
        first_name="Inactive", last_name="Student",
        is_active=False,
    ))
    # Soft-deleted — not counted (gone from tenant).
    db.add(Student(
        id=uuid.uuid4(), tenant_id=tid,
        first_name="Deleted", last_name="Student",
        is_active=True, deleted_at=datetime.now(timezone.utc),
    ))
    await db.commit()

    yield {"tid": tid, "tenant": tenant}

    from sqlalchemy import text as _text
    async with db.bind.connect() as conn:
        await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": tid})
        await conn.commit()


class TestCountActiveStudents:
    @pytest.mark.asyncio
    async def test_counts_only_active_undeleted_students(
        self, db, tenant_with_students,
    ):
        svc = get_subscription_service()
        count = await svc.count_active_students(db, tenant_with_students["tid"])
        # 3 active, 1 inactive (skipped), 1 soft-deleted (skipped) → 3.
        assert count == 3

    @pytest.mark.asyncio
    async def test_empty_tenant_returns_zero(self, db):
        tid = uuid.uuid4()
        tenant = Tenant(
            id=tid, name=f"Empty {tid.hex[:6]}", slug=f"empty-{tid.hex[:8]}",
            email=f"admin@{tid.hex[:6]}.example.com",
            education_type="PRIMARY_SCHOOL",
            settings={},
            is_active=True, onboarding_completed=True,
        )
        db.add(tenant)
        await db.commit()
        try:
            svc = get_subscription_service()
            count = await svc.count_active_students(db, tid)
            assert count == 0
        finally:
            from sqlalchemy import text as _text
            async with db.bind.connect() as conn:
                await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": tid})
                await conn.commit()


class TestPreviewCycleAmount:
    @pytest.mark.asyncio
    async def test_hybrid_preview_shape(self, db, tenant_with_students):
        """UI payload contract — one dict shape across all three
        charge models. HYBRID with 3 active students, $20 base +
        $1/student → $23."""
        plan = SubscriptionPlan(
            id=uuid.uuid4(), name="GROWTH",
            price_monthly=Decimal("20.00"),
            charge_model=ChargeModel.HYBRID.value,
            per_student_amount=Decimal("1.00"),
            currency="USD",
        )
        svc = get_subscription_service()
        result = await svc.preview_cycle_amount(
            db, tenant_with_students["tid"], plan, "MONTHLY",
        )
        assert result == {
            "charge_model": "HYBRID",
            "base_amount": "20.00",
            "per_student_amount": "1.00",
            "student_count": 3,
            "total_amount": "23.00",
            "currency": "USD",
            "billing_frequency": "MONTHLY",
        }

    @pytest.mark.asyncio
    async def test_per_student_preview_zero_base(self, db, tenant_with_students):
        plan = SubscriptionPlan(
            id=uuid.uuid4(), name="PAYG",
            price_monthly=Decimal("50.00"),  # base ignored for PER_STUDENT
            charge_model=ChargeModel.PER_STUDENT.value,
            per_student_amount=Decimal("2.00"),
            currency="USD",
        )
        svc = get_subscription_service()
        result = await svc.preview_cycle_amount(
            db, tenant_with_students["tid"], plan, "MONTHLY",
        )
        # base_amount is reported as 0 for PER_STUDENT so the UI can
        # render "$0 base + $2/student × 3 = $6" or similar without
        # mistakenly showing the (ignored) $50 price_monthly.
        assert result["base_amount"] == "0.00"
        assert result["per_student_amount"] == "2.00"
        assert result["student_count"] == 3
        assert result["total_amount"] == "6.00"
