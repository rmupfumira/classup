"""Hard student deletion — 2026-10-09.

School admin's "Delete Student" is a full purge:
- The student row is physically deleted (not soft-flagged).
- Every ON DELETE CASCADE child goes with it — attendance, reports,
  invoices, payments, parent_students, invitations, photo/doc share
  recipients.
- Orphan parents (parents whose only child at this tenant was this
  one) are ALSO hard-deleted so their email + phone drop out of the
  UNIQUE index and can be reused immediately on the same tenant.
- Parents with a sibling still enrolled are left alone.
- The preview method returns truthful counts so the confirmation UI
  can show the admin exactly what will happen.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AttendanceRecord,
    BillingInvoice,
    DailyReport,
    ParentInvitation,
    ReportTemplate,
    SchoolClass,
    Student,
    Tenant,
    User,
)
from app.models.invitation import InvitationStatus
from app.models.student import ParentStudent
from app.models.user import Role
from app.services.student_service import get_student_service
from app.utils.security import hash_password


@pytest_asyncio.fixture
async def rich_family(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass, test_admin: User,
):
    """A student + a solo parent (one child only) + attendance + reports
    + invoice + a pending invitation. Returns everything so tests can
    reach each piece after the deletion."""
    student = Student(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        first_name="Hard", last_name="Delete",
        class_id=test_class.id, is_active=True,
    )
    parent = User(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        email=f"solo-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Solo", last_name="Parent",
        phone="+263770001111",
        role=Role.PARENT.value, is_active=True,
    )
    db.add_all([student, parent])
    await db.flush()

    db.add(ParentStudent(
        id=uuid.uuid4(), parent_id=parent.id, student_id=student.id,
        relationship_type="PARENT", is_primary=True,
    ))

    # Attendance
    today = date.today()
    for i in range(3):
        db.add(AttendanceRecord(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            student_id=student.id, class_id=test_class.id,
            date=today - timedelta(days=i),
            status="PRESENT", recorded_by=test_admin.id,
        ))

    # Report (needs a template)
    tmpl = ReportTemplate(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        name="Report", report_type="DAILY_ACTIVITY",
        frequency="DAILY", applies_to_grade_level="",
        sections=[], display_order=0, is_active=True,
    )
    db.add(tmpl)
    await db.flush()
    db.add(DailyReport(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        student_id=student.id, class_id=test_class.id,
        template_id=tmpl.id, report_date=today,
        report_data={}, status="DRAFT",
        created_by=test_admin.id,
    ))

    # Invoice
    db.add(BillingInvoice(
        id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=student.id,
        invoice_number="INV-HD-0001",
        billing_period_start=today, billing_period_end=today,
        due_date=today, subtotal=Decimal("50"), total_amount=Decimal("50"),
        amount_paid=Decimal("0"), balance=Decimal("50"),
        status="SENT", created_by=test_admin.id,
    ))

    # Pending invitation for the same student
    db.add(ParentInvitation(
        id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=student.id,
        email="newparent@example.com",
        first_name="N", last_name="P",
        invitation_code="HDTEST01",
        status=InvitationStatus.PENDING.value,
        created_by=test_admin.id,
        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    ))

    await db.commit()
    return {
        "student": student,
        "parent": parent,
        "parent_email": parent.email,
        "parent_phone": parent.phone,
    }


@pytest_asyncio.fixture
async def two_sibling_family(
    db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass,
):
    """A parent with TWO children on the tenant. Deleting one child
    should NOT touch the parent — the sibling is still enrolled."""
    parent = User(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        email=f"twin-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Twin", last_name="Parent",
        role=Role.PARENT.value, is_active=True,
    )
    child_a = Student(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        first_name="Elder", last_name="Twin",
        class_id=test_class.id, is_active=True,
    )
    child_b = Student(
        id=uuid.uuid4(), tenant_id=test_tenant.id,
        first_name="Younger", last_name="Twin",
        class_id=test_class.id, is_active=True,
    )
    db.add_all([parent, child_a, child_b])
    await db.flush()
    db.add_all([
        ParentStudent(id=uuid.uuid4(), parent_id=parent.id, student_id=child_a.id,
                      relationship_type="PARENT", is_primary=True),
        ParentStudent(id=uuid.uuid4(), parent_id=parent.id, student_id=child_b.id,
                      relationship_type="PARENT", is_primary=False),
    ])
    await db.commit()
    return {"parent": parent, "child_a": child_a, "child_b": child_b}


# ---------------------------------------------------------------------------
# Preview — reads nothing is destroyed
# ---------------------------------------------------------------------------

class TestPreview:
    @pytest.mark.asyncio
    async def test_counts_are_truthful(
        self, db: AsyncSession, rich_family,
    ):
        preview = await get_student_service().preview_student_deletion(
            db, rich_family["student"].id,
        )
        counts = preview["counts"]
        assert counts["attendance_records"] == 3
        assert counts["reports"] == 1
        assert counts["invoices"] == 1
        assert counts["pending_invitations"] == 1
        # Solo parent will be removed; nobody is kept
        assert counts["parents_removed"] == 1
        assert counts["parents_kept"] == 0
        assert len(preview["parents_to_remove"]) == 1
        assert preview["parents_to_remove"][0]["email"] == rich_family["parent_email"]

    @pytest.mark.asyncio
    async def test_sibling_parent_is_kept(
        self, db: AsyncSession, two_sibling_family,
    ):
        preview = await get_student_service().preview_student_deletion(
            db, two_sibling_family["child_a"].id,
        )
        counts = preview["counts"]
        assert counts["parents_removed"] == 0
        assert counts["parents_kept"] == 1
        kept = preview["parents_kept"][0]
        assert kept["other_children"] == 1  # Younger twin still enrolled

    @pytest.mark.asyncio
    async def test_preview_does_not_mutate(
        self, db: AsyncSession, rich_family,
    ):
        """Preview is read-only — nothing should be deleted or flagged."""
        sid = rich_family["student"].id
        await get_student_service().preview_student_deletion(db, sid)
        still_there = (await db.execute(
            select(func.count(Student.id)).where(Student.id == sid)
        )).scalar()
        assert still_there == 1


# ---------------------------------------------------------------------------
# Hard delete — everything goes
# ---------------------------------------------------------------------------

class TestHardDelete:
    @pytest.mark.asyncio
    async def test_student_row_physically_gone(
        self, db: AsyncSession, rich_family,
    ):
        sid = rich_family["student"].id
        await get_student_service().hard_delete_student(db, sid)
        await db.commit()
        cnt = (await db.execute(
            select(func.count(Student.id)).where(Student.id == sid)
        )).scalar()
        assert cnt == 0

    @pytest.mark.asyncio
    async def test_cascades_remove_related_rows(
        self, db: AsyncSession, rich_family,
    ):
        sid = rich_family["student"].id
        await get_student_service().hard_delete_student(db, sid)
        await db.commit()

        attendance = (await db.execute(
            select(func.count(AttendanceRecord.id))
            .where(AttendanceRecord.student_id == sid)
        )).scalar()
        reports = (await db.execute(
            select(func.count(DailyReport.id))
            .where(DailyReport.student_id == sid)
        )).scalar()
        invoices = (await db.execute(
            select(func.count(BillingInvoice.id))
            .where(BillingInvoice.student_id == sid)
        )).scalar()
        invites = (await db.execute(
            select(func.count(ParentInvitation.id))
            .where(ParentInvitation.student_id == sid)
        )).scalar()
        joins = (await db.execute(
            select(func.count(ParentStudent.id))
            .where(ParentStudent.student_id == sid)
        )).scalar()

        assert attendance == 0
        assert reports == 0
        assert invoices == 0
        assert invites == 0
        assert joins == 0

    @pytest.mark.asyncio
    async def test_orphan_parent_hard_deleted_so_email_reusable(
        self, db: AsyncSession, test_tenant: Tenant, rich_family,
    ):
        """The whole point: the parent's email and phone must become
        reusable immediately on the same tenant."""
        parent_email = rich_family["parent_email"]
        parent_phone = rich_family["parent_phone"]

        await get_student_service().hard_delete_student(
            db, rich_family["student"].id,
        )
        await db.commit()

        # Parent User row is gone.
        remaining = (await db.execute(
            select(func.count(User.id)).where(
                User.tenant_id == test_tenant.id,
                User.email == parent_email,
            )
        )).scalar()
        assert remaining == 0

        # Email can be used NOW to create a brand new user — no stale
        # UNIQUE conflict, no soft-deleted tombstone to work around.
        fresh_parent = User(
            id=uuid.uuid4(), tenant_id=test_tenant.id,
            email=parent_email,          # same email
            phone=parent_phone,          # same phone
            password_hash=hash_password("fresh"),
            first_name="Fresh", last_name="Signup",
            role=Role.PARENT.value, is_active=True,
        )
        db.add(fresh_parent)
        await db.commit()  # must not raise IntegrityError

    @pytest.mark.asyncio
    async def test_sibling_parent_is_not_deleted(
        self, db: AsyncSession, two_sibling_family,
    ):
        """Only the deleted child's parent_students row is removed; the
        parent User + the sibling child both stay."""
        parent_id = two_sibling_family["parent"].id
        sibling_id = two_sibling_family["child_b"].id

        await get_student_service().hard_delete_student(
            db, two_sibling_family["child_a"].id,
        )
        await db.commit()

        # Parent still alive
        p = await db.get(User, parent_id)
        assert p is not None
        # Sibling still enrolled
        s = await db.get(Student, sibling_id)
        assert s is not None
        # And still linked
        link_count = (await db.execute(
            select(func.count(ParentStudent.id)).where(
                ParentStudent.parent_id == parent_id,
                ParentStudent.student_id == sibling_id,
            )
        )).scalar()
        assert link_count == 1

    @pytest.mark.asyncio
    async def test_summary_reports_parents_removed(
        self, db: AsyncSession, rich_family,
    ):
        summary = await get_student_service().hard_delete_student(
            db, rich_family["student"].id,
        )
        await db.commit()
        assert summary["parents_removed"] == 1
        assert "Hard Delete" in summary["student_name"]
