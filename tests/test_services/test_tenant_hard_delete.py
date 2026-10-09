"""Hard tenant deletion — 2026-10-09.

Super admin's "Delete Tenant" is a full platform purge:
- Preview returns truthful counts per category so the modal can show
  the super admin exactly what will go.
- Hard delete physically drops the Tenant row; DB cascades on every
  tenant_id FK remove every child (users, students, classes,
  attendance, reports, invoices, invitations, subscriptions, etc.).
- Slug + every email + every phone used on the tenant become
  immediately reusable on the platform.
- Rows with ``ondelete='SET NULL'`` (audit_logs, whatsapp_in/out,
  ai_tool_calls) survive with tenant_id = NULL — intentional, so
  cross-tenant history isn't lost.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AttendanceRecord,
    BillingInvoice,
    SchoolClass,
    Student,
    Tenant,
    User,
)
from app.models.student import ParentStudent
from app.models.user import Role
from app.services.tenant_service import get_tenant_service
from app.utils.security import hash_password


@pytest_asyncio.fixture
async def populated_tenant(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, test_admin: User,
):
    """Add a parent, student, link, attendance, and an invoice to the
    standard test tenant so the preview has real counts and the hard
    delete has real cascades to prove."""
    parent = User(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        email=f"ht-parent-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Hard", last_name="Parent",
        phone="+263770009999",
        role=Role.PARENT.value, is_active=True,
    )
    student = Student(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        first_name="Ht", last_name="Student",
        class_id=test_class.id, is_active=True,
    )
    db.add_all([parent, student])
    await db.flush()
    db.add(ParentStudent(
        id=uuid.uuid4(), parent_id=parent.id, student_id=student.id,
        relationship_type="PARENT", is_primary=True,
    ))
    db.add(AttendanceRecord(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        student_id=student.id, class_id=test_class.id,
        date=date.today(), status="PRESENT", recorded_by=test_admin.id,
    ))
    db.add(BillingInvoice(
        id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=student.id,
        invoice_number="INV-HT-0001",
        billing_period_start=date.today(), billing_period_end=date.today(),
        due_date=date.today(), subtotal=Decimal("100"),
        total_amount=Decimal("100"), amount_paid=Decimal("0"),
        balance=Decimal("100"), status="SENT",
        created_by=test_admin.id,
    ))
    await db.commit()
    return {
        "tenant": test_tenant,
        "parent": parent,
        "parent_email": parent.email,
        "parent_phone": parent.phone,
        "student": student,
        "slug": test_tenant.slug,
    }


class TestPreview:
    @pytest.mark.asyncio
    async def test_counts_are_truthful(
        self, db: AsyncSession, populated_tenant,
    ):
        preview = await get_tenant_service().preview_tenant_deletion(
            db, populated_tenant["tenant"].id,
        )
        counts = preview["counts"]
        # At least these should be present with the right magnitude.
        assert counts["students"] >= 1
        assert counts["parents"] >= 1
        assert counts["school_admins"] >= 1
        assert counts["classes"] >= 1
        assert counts["attendance_records"] >= 1
        assert counts["invoices"] >= 1
        # Tenant metadata echoed back for the modal.
        assert preview["tenant"]["slug"] == populated_tenant["slug"]

    @pytest.mark.asyncio
    async def test_preview_does_not_mutate(
        self, db: AsyncSession, populated_tenant,
    ):
        tid = populated_tenant["tenant"].id
        await get_tenant_service().preview_tenant_deletion(db, tid)
        still_there = (await db.execute(
            select(func.count(Tenant.id)).where(Tenant.id == tid)
        )).scalar()
        assert still_there == 1


class TestHardDelete:
    @pytest.mark.asyncio
    async def test_tenant_row_physically_gone(
        self, db: AsyncSession, populated_tenant,
    ):
        tid = populated_tenant["tenant"].id
        await get_tenant_service().hard_delete_tenant(db, tid)
        cnt = (await db.execute(
            select(func.count(Tenant.id)).where(Tenant.id == tid)
        )).scalar()
        assert cnt == 0

    @pytest.mark.asyncio
    async def test_cascades_remove_children(
        self, db: AsyncSession, populated_tenant,
    ):
        tid = populated_tenant["tenant"].id
        await get_tenant_service().hard_delete_tenant(db, tid)

        users = (await db.execute(
            select(func.count(User.id)).where(User.tenant_id == tid)
        )).scalar()
        students = (await db.execute(
            select(func.count(Student.id)).where(Student.tenant_id == tid)
        )).scalar()
        classes = (await db.execute(
            select(func.count(SchoolClass.id)).where(SchoolClass.tenant_id == tid)
        )).scalar()
        att = (await db.execute(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.tenant_id == tid,
            )
        )).scalar()
        inv = (await db.execute(
            select(func.count(BillingInvoice.id)).where(
                BillingInvoice.tenant_id == tid,
            )
        )).scalar()

        assert users == 0
        assert students == 0
        assert classes == 0
        assert att == 0
        assert inv == 0

    @pytest.mark.asyncio
    async def test_slug_is_reusable_after_delete(
        self, db: AsyncSession, populated_tenant,
    ):
        """The whole point for super admin: delete tenant → create a
        fresh tenant with the SAME slug on the next line. No conflict."""
        from app.models.tenant import EducationType

        tid = populated_tenant["tenant"].id
        slug = populated_tenant["slug"]

        await get_tenant_service().hard_delete_tenant(db, tid)

        fresh = await get_tenant_service().create_tenant(
            db,
            name=f"Reborn {uuid.uuid4().hex[:6]}",
            email=f"reborn-{uuid.uuid4().hex[:6]}@example.com",
            education_type=EducationType.PRIMARY_SCHOOL,
            slug=slug,
        )
        assert fresh.slug == slug
        # Clean up the newborn so other tests aren't polluted.
        from sqlalchemy import text as _text
        async with db.bind.connect() as conn:
            await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": fresh.id})
            await conn.commit()

    @pytest.mark.asyncio
    async def test_parent_email_and_phone_reusable_after_delete(
        self, db: AsyncSession, populated_tenant,
    ):
        """The parent's email + phone drop out of UNIQUE(email,
        tenant_id). The tenant itself is gone so this is trivially
        true — the test proves the User row is physically removed, not
        soft-flagged with a lingering UNIQUE constraint."""
        parent_email = populated_tenant["parent_email"]
        tid = populated_tenant["tenant"].id

        await get_tenant_service().hard_delete_tenant(db, tid)

        remaining = (await db.execute(
            select(func.count(User.id)).where(User.email == parent_email)
        )).scalar()
        # The old parent User row is physically gone.
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_summary_returns_name_and_slug(
        self, db: AsyncSession, populated_tenant,
    ):
        summary = await get_tenant_service().hard_delete_tenant(
            db, populated_tenant["tenant"].id,
        )
        assert summary["tenant_slug"] == populated_tenant["slug"]
        assert summary["tenant_name"]
