"""Role-level data scoping across services (2026-11 access-control audit).

Same class of bug as the parent-sees-every-report leak: queries that
filtered by tenant but not by role ownership. Each test pins one rule
from the permission matrix:
  - parent A never reaches parent B's child's data
  - a teacher never reaches another class's data
  - parents never see drafts
  - nothing crosses tenants
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ForbiddenException, NotFoundException
from app.models import (
    AttendanceRecord,
    BillingInvoice,
    FileEntity,
    ParentInvitation,
    SchoolClass,
    SchoolEvent,
    Student,
    Subject,
    Tenant,
    User,
)
from app.models.announcement import Announcement
from app.models.exam import Exam, ExamSubject
from app.models.message import Message, MessageRecipient
from app.models.photo_share import PhotoShare, PhotoShareFile
from app.models.school_class import TeacherClass
from app.models.student import ParentStudent
from app.models.user import Role
from app.schemas.attendance import AttendanceStatus, BulkAttendanceCreate
from app.services import event_service
from app.services.announcement_service import get_announcement_service
from app.services.attendance_service import get_attendance_service
from app.services.billing_service import get_billing_service
from app.services.class_service import get_class_service
from app.services.exam_service import get_exam_service
from app.services.file_service import get_file_service
from app.services.invitation_service import get_invitation_service
from app.services.message_service import get_message_service
from app.services.student_service import get_student_service
from app.utils.security import hash_password
from app.utils.tenant_context import _current_user_id, _current_user_role


@contextlib.contextmanager
def as_user(user: User):
    """Run service calls as ``user`` (role + id context)."""
    t_id = _current_user_id.set(user.id)
    t_role = _current_user_role.set(user.role)
    try:
        yield
    finally:
        _current_user_id.reset(t_id)
        _current_user_role.reset(t_role)


def _user(tenant_id, role: str, name: str) -> User:
    return User(
        id=uuid.uuid4(), tenant_id=tenant_id,
        email=f"{name}-{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password("x"),
        first_name=name.title(), last_name="Scope",
        role=role, is_active=True, language="en",
    )


@pytest_asyncio.fixture
async def world(db: AsyncSession, test_tenant: Tenant, test_admin: User, test_class: SchoolClass):
    """Two classes (A = test_class, B), a teacher on A, parent A with a
    child in A, parent B with a child in B, plus a second tenant with its
    own class / student / parent."""
    class_b = SchoolClass(id=uuid.uuid4(), tenant_id=test_tenant.id, name="Grade 5B", is_active=True)
    teacher = _user(test_tenant.id, Role.TEACHER.value, "teacher")
    parent_a = _user(test_tenant.id, Role.PARENT.value, "parenta")
    parent_b = _user(test_tenant.id, Role.PARENT.value, "parentb")
    db.add_all([class_b, teacher, parent_a, parent_b])
    await db.flush()

    child_a = Student(id=uuid.uuid4(), tenant_id=test_tenant.id, first_name="Ann",
                      last_name="A", class_id=test_class.id, is_active=True)
    child_b = Student(id=uuid.uuid4(), tenant_id=test_tenant.id, first_name="Ben",
                      last_name="B", class_id=class_b.id, is_active=True)
    db.add_all([child_a, child_b])
    await db.flush()
    db.add_all([
        TeacherClass(id=uuid.uuid4(), teacher_id=teacher.id, class_id=test_class.id, is_primary=True),
        ParentStudent(id=uuid.uuid4(), parent_id=parent_a.id, student_id=child_a.id,
                      relationship_type="PARENT", is_primary=True),
        ParentStudent(id=uuid.uuid4(), parent_id=parent_b.id, student_id=child_b.id,
                      relationship_type="PARENT", is_primary=True),
    ])

    other_tenant = Tenant(
        id=uuid.uuid4(), name="Other School", slug=f"other-{uuid.uuid4().hex[:8]}",
        email=f"other-{uuid.uuid4().hex[:8]}@test.local", is_active=True,
    )
    db.add(other_tenant)
    await db.flush()
    foreign_class = SchoolClass(id=uuid.uuid4(), tenant_id=other_tenant.id, name="Foreign", is_active=True)
    foreign_parent = _user(other_tenant.id, Role.PARENT.value, "foreign")
    db.add_all([foreign_class, foreign_parent])
    await db.flush()
    foreign_student = Student(id=uuid.uuid4(), tenant_id=other_tenant.id, first_name="Fay",
                              last_name="F", class_id=foreign_class.id, is_active=True)
    db.add(foreign_student)
    await db.flush()
    db.add(ParentStudent(id=uuid.uuid4(), parent_id=foreign_parent.id, student_id=foreign_student.id,
                         relationship_type="PARENT", is_primary=True))
    await db.commit()
    other_tenant_id = other_tenant.id

    try:
        yield {
            "admin": test_admin, "teacher": teacher,
            "class_a": test_class, "class_b": class_b,
            "parent_a": parent_a, "parent_b": parent_b,
            "child_a": child_a, "child_b": child_b,
            "other_tenant": other_tenant, "foreign_class": foreign_class,
            "foreign_student": foreign_student, "foreign_parent": foreign_parent,
        }
    finally:
        await db.execute(delete(Tenant).where(Tenant.id == other_tenant_id))
        await db.commit()


def _last_weekday() -> date:
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ─────────────────────────── Students ───────────────────────────


class TestStudentScope:
    async def test_parent_only_reaches_own_child(self, db, world):
        svc = get_student_service()
        with as_user(world["parent_a"]):
            assert (await svc.get_student(db, world["child_a"].id)).id == world["child_a"].id
            with pytest.raises(NotFoundException):
                await svc.get_student(db, world["child_b"].id)

    async def test_teacher_only_reaches_own_class(self, db, world):
        svc = get_student_service()
        with as_user(world["teacher"]):
            assert (await svc.get_student(db, world["child_a"].id)).id == world["child_a"].id
            with pytest.raises(NotFoundException):
                await svc.get_student(db, world["child_b"].id)
            with pytest.raises(NotFoundException):
                await svc.get_student_parents(db, world["child_b"].id)

    async def test_admin_reaches_whole_tenant_but_not_other_tenant(self, db, world):
        svc = get_student_service()
        with as_user(world["admin"]):
            assert (await svc.get_student(db, world["child_b"].id)).id == world["child_b"].id
            with pytest.raises(NotFoundException):
                await svc.get_student(db, world["foreign_student"].id)

    async def test_parent_editable_still_forbidden_for_other_child(self, db, world):
        from app.schemas.student import StudentParentEditable

        with as_user(world["parent_a"]):
            with pytest.raises(ForbiddenException):
                await get_student_service().update_student_parent_editable(
                    db, world["child_b"].id, world["parent_a"].id,
                    StudentParentEditable(allergies="x"),
                )


# ─────────────────────────── Classes ───────────────────────────


class TestClassScope:
    async def test_teacher_cannot_open_other_class(self, db, world):
        svc = get_class_service()
        with as_user(world["teacher"]):
            assert (await svc.get_class(db, world["class_a"].id)).id == world["class_a"].id
            with pytest.raises(NotFoundException):
                await svc.get_class(db, world["class_b"].id)
            with pytest.raises(NotFoundException):
                await svc.get_class_students(db, world["class_b"].id)

    async def test_admin_cannot_open_foreign_class(self, db, world):
        with as_user(world["admin"]):
            assert (await get_class_service().get_class(db, world["class_b"].id))
            with pytest.raises(NotFoundException):
                await get_class_service().get_class(db, world["foreign_class"].id)

    async def test_subject_teacher_does_not_count_as_class_teacher(self, db, world, test_tenant):
        from app.models.academic import ClassSubjectTeacher

        subj = Subject(id=uuid.uuid4(), tenant_id=test_tenant.id, name="Art",
                       code=f"ART-{uuid.uuid4().hex[:4]}", is_active=True)
        db.add(subj)
        await db.flush()
        db.add(ClassSubjectTeacher(id=uuid.uuid4(), class_id=world["class_b"].id,
                                   subject_id=subj.id, teacher_id=world["teacher"].id))
        test_tenant.settings = {**test_tenant.settings, "subject_teacher_enabled": True}
        await db.commit()
        with as_user(world["teacher"]):
            with pytest.raises(NotFoundException):
                await get_class_service().get_class(db, world["class_b"].id)
            with pytest.raises(NotFoundException):
                await get_student_service().get_student(db, world["child_b"].id)


# ─────────────────────────── Attendance ───────────────────────────


@pytest_asyncio.fixture
async def attendance(db, world, test_tenant):
    day = _last_weekday()
    rec_a = AttendanceRecord(id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=world["child_a"].id,
                             class_id=world["class_a"].id, date=day, status="PRESENT",
                             recorded_by=world["admin"].id)
    rec_b = AttendanceRecord(id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=world["child_b"].id,
                             class_id=world["class_b"].id, date=day, status="ABSENT",
                             recorded_by=world["admin"].id)
    db.add_all([rec_a, rec_b])
    await db.commit()
    return {"day": day, "a": rec_a, "b": rec_b}


class TestAttendanceScope:
    async def test_parent_history_only_for_own_child(self, db, world, attendance):
        svc = get_attendance_service()
        with as_user(world["parent_a"]):
            records, total, _ = await svc.get_student_attendance_history(
                db, world["child_a"].id, date_from=attendance["day"], date_to=attendance["day"],
            )
            assert [r.id for r in records] == [attendance["a"].id]
            with pytest.raises(NotFoundException):
                await svc.get_student_attendance_history(db, world["child_b"].id)

    async def test_parent_record_list_is_own_children_only(self, db, world, attendance):
        with as_user(world["parent_a"]):
            records, total = await get_attendance_service().get_attendance_records(db)
        assert {r.id for r in records} == {attendance["a"].id}

    async def test_teacher_cannot_read_or_edit_other_class(self, db, world, attendance):
        from app.schemas.attendance import AttendanceRecordUpdate

        svc = get_attendance_service()
        with as_user(world["teacher"]):
            records, _ = await svc.get_attendance_records(db)
            assert {r.id for r in records} == {attendance["a"].id}
            with pytest.raises(NotFoundException):
                await svc.get_attendance_record(db, attendance["b"].id)
            with pytest.raises(NotFoundException):
                await svc.update_attendance_record(
                    db, attendance["b"].id, AttendanceRecordUpdate(notes="x"),
                )
            with pytest.raises(NotFoundException):
                await svc.get_class_attendance_for_date(db, world["class_b"].id, attendance["day"])
            stats = await svc.get_attendance_stats(
                db, date_from=attendance["day"], date_to=attendance["day"],
            )
            assert stats.absent_count == 0 and stats.present_count == 1

    async def test_bulk_rejects_out_of_scope_and_foreign_students(self, db, world):
        day = _last_weekday() - timedelta(days=7)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        payload = BulkAttendanceCreate(
            class_id=world["class_a"].id, date=day,
            records=[
                {"student_id": world["child_a"].id, "status": AttendanceStatus.PRESENT},
                {"student_id": world["child_b"].id, "status": AttendanceStatus.PRESENT},
                {"student_id": world["foreign_student"].id, "status": AttendanceStatus.PRESENT},
            ],
        )
        with as_user(world["teacher"]):
            result = await get_attendance_service().record_bulk_attendance(db, payload)
        assert result.success_count == 1 and result.error_count == 2
        written = (await db.execute(
            select(AttendanceRecord.student_id).where(
                AttendanceRecord.date == day,
                AttendanceRecord.student_id.in_([
                    world["child_a"].id, world["child_b"].id, world["foreign_student"].id,
                ]),
            )
        )).scalars().all()
        assert set(written) == {world["child_a"].id}

        with as_user(world["teacher"]):
            with pytest.raises(NotFoundException):
                await get_attendance_service().record_bulk_attendance(db, BulkAttendanceCreate(
                    class_id=world["class_b"].id, date=day,
                    records=[{"student_id": world["child_b"].id, "status": AttendanceStatus.PRESENT}],
                ))


# ─────────────────────────── Messages ───────────────────────────


class TestMessageScope:
    async def _thread(self, db, world, test_tenant):
        root = Message(id=uuid.uuid4(), tenant_id=test_tenant.id, sender_id=world["admin"].id,
                       message_type="STUDENT_MESSAGE", body="About Ben",
                       student_id=world["child_b"].id, class_id=world["class_b"].id)
        db.add(root)
        await db.flush()
        db.add(MessageRecipient(id=uuid.uuid4(), message_id=root.id, user_id=world["parent_b"].id))
        await db.commit()
        return root

    async def test_non_participant_cannot_read_thread(self, db, world, test_tenant):
        root = await self._thread(db, world, test_tenant)
        svc = get_message_service()
        for outsider in (world["parent_a"], world["teacher"]):
            with as_user(outsider):
                with pytest.raises(NotFoundException):
                    await svc.get_conversation_messages(db, root.id)
                with pytest.raises(ValueError):
                    await svc.reply_to_thread(db, root.id, "sneaky")
        with as_user(world["parent_b"]):
            msgs, total = await svc.get_conversation_messages(db, root.id)
            assert total == 1
        with as_user(world["admin"]):
            _, total = await svc.get_conversation_messages(db, root.id)
            assert total == 1


# ─────────────────────────── Files ───────────────────────────


class TestFileScope:
    async def test_file_only_reachable_through_visible_share(self, db, world, test_tenant):
        fe = FileEntity(id=uuid.uuid4(), tenant_id=test_tenant.id, storage_path="x/y.jpg",
                        original_name="ben.jpg", content_type="image/jpeg", file_size=10,
                        file_category="PHOTO", uploaded_by=world["admin"].id)
        db.add(fe)
        await db.flush()
        share = PhotoShare(id=uuid.uuid4(), tenant_id=test_tenant.id, class_id=world["class_b"].id,
                           shared_by=world["admin"].id)
        db.add(share)
        await db.flush()
        db.add(PhotoShareFile(id=uuid.uuid4(), photo_share_id=share.id, file_entity_id=fe.id))
        await db.commit()

        svc = get_file_service()
        for outsider in (world["parent_a"], world["teacher"]):
            with as_user(outsider):
                with pytest.raises(NotFoundException):
                    await svc.get_file(db, fe.id)
        with as_user(world["parent_b"]):
            assert (await svc.get_file(db, fe.id)).id == fe.id
        with as_user(world["admin"]):
            assert (await svc.get_file(db, fe.id)).id == fe.id
        with as_user(world["teacher"]):
            files, total = await svc.get_files(db)
            assert fe.id not in {f.id for f in files}


# ─────────────────────────── Billing ───────────────────────────


@pytest_asyncio.fixture
async def invoices(db, world, test_tenant):
    def inv(student, status, n, amount):
        return BillingInvoice(
            id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=student.id,
            invoice_number=f"INV-SCOPE-{n}-{uuid.uuid4().hex[:4]}", due_date=date.today(),
            subtotal=Decimal(amount), total_amount=Decimal(amount),
            amount_paid=Decimal("0"), balance=Decimal(amount),
            status=status, created_by=world["admin"].id,
        )
    sent_a = inv(world["child_a"], "SENT", 1, "100.00")
    draft_a = inv(world["child_a"], "DRAFT", 2, "999.00")
    sent_b = inv(world["child_b"], "SENT", 3, "50.00")
    db.add_all([sent_a, draft_a, sent_b])
    await db.commit()
    return {"sent_a": sent_a, "draft_a": draft_a, "sent_b": sent_b}


class TestBillingScope:
    async def test_parent_invoice_access(self, db, world, invoices):
        svc = get_billing_service()
        with as_user(world["parent_a"]):
            assert (await svc.get_invoice(db, invoices["sent_a"].id)).id == invoices["sent_a"].id
            for hidden in ("draft_a", "sent_b"):
                with pytest.raises(NotFoundException):
                    await svc.get_invoice(db, invoices[hidden].id)
                with pytest.raises(NotFoundException):
                    await svc.verify_parent_access(db, world["parent_a"].id, invoices[hidden].id)

    async def test_parent_statement_and_balance(self, db, world, invoices):
        svc = get_billing_service()
        with as_user(world["parent_a"]):
            statement = await svc.get_student_statement(db, world["child_a"].id)
            assert statement.total_invoiced == Decimal("100.00")
            assert (await svc.get_student_balance(db, world["child_a"].id))["balance"] == Decimal("100.00")
            with pytest.raises(NotFoundException):
                await svc.get_student_statement(db, world["child_b"].id)
            with pytest.raises(NotFoundException):
                await svc.get_student_balance(db, world["child_b"].id)

    async def test_parent_invoice_list_honours_student_id(self, db, world, invoices, test_tenant):
        sibling = Student(id=uuid.uuid4(), tenant_id=test_tenant.id, first_name="Sib",
                          last_name="A", class_id=world["class_a"].id, is_active=True)
        db.add(sibling)
        await db.flush()
        db.add(ParentStudent(id=uuid.uuid4(), parent_id=world["parent_a"].id, student_id=sibling.id,
                             relationship_type="PARENT", is_primary=True))
        sib_inv = BillingInvoice(
            id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=sibling.id,
            invoice_number=f"INV-SIB-{uuid.uuid4().hex[:4]}", due_date=date.today(),
            subtotal=Decimal("10"), total_amount=Decimal("10"), amount_paid=Decimal("0"),
            balance=Decimal("10"), status="SENT", created_by=world["admin"].id,
        )
        db.add(sib_inv)
        await db.commit()
        svc = get_billing_service()
        parent_id = world["parent_a"].id
        with as_user(world["parent_a"]):
            all_rows, _ = await svc.get_parent_invoices(db, parent_id)
            assert {i.id for i in all_rows} == {invoices["sent_a"].id, sib_inv.id}
            only_a, total = await svc.get_parent_invoices(db, parent_id, student_id=world["child_a"].id)
            assert [i.id for i in only_a] == [invoices["sent_a"].id] and total == 1
            other, total = await svc.get_parent_invoices(db, parent_id, student_id=world["child_b"].id)
            assert other == [] and total == 0

    async def test_teacher_has_no_billing_access(self, db, world, invoices):
        with as_user(world["teacher"]):
            with pytest.raises(NotFoundException):
                await get_billing_service().get_invoice(db, invoices["sent_a"].id)

    async def test_admin_sees_drafts(self, db, world, invoices):
        svc = get_billing_service()
        with as_user(world["admin"]):
            assert (await svc.get_invoice(db, invoices["draft_a"].id)).status == "DRAFT"
            statement = await svc.get_student_statement(db, world["child_a"].id)
            assert statement.total_invoiced == Decimal("1099.00")


# ─────────────────────────── Events ───────────────────────────


class TestEventScope:
    def _event(self, tenant_id, created_by, **kw):
        now = datetime.now(timezone.utc) + timedelta(days=3)
        return SchoolEvent(
            id=uuid.uuid4(), tenant_id=tenant_id, created_by=created_by,
            title="Meeting", event_type="PARENT_MEETING", starts_at=now, **kw,
        )

    async def test_parent_only_sees_events_for_own_children(self, db, world, test_tenant):
        ev = self._event(test_tenant.id, world["admin"].id, scope="STUDENT", student_id=world["child_b"].id)
        school = self._event(test_tenant.id, world["admin"].id, scope="SCHOOL")
        db.add_all([ev, school])
        await db.commit()
        assert not await event_service.parent_can_see_event(db, ev, world["parent_a"].id)
        assert await event_service.parent_can_see_event(db, ev, world["parent_b"].id)
        assert await event_service.parent_can_see_event(db, school, world["parent_a"].id)

    async def test_audience_never_crosses_tenants(self, db, world, test_tenant):
        ev = self._event(test_tenant.id, world["admin"].id, scope="CLASS", class_id=world["foreign_class"].id)
        db.add(ev)
        await db.commit()
        assert await event_service.resolve_audience(db, ev) == []


# ─────────────────────────── Announcements ───────────────────────────


class TestAnnouncementScope:
    async def test_class_announcement_only_for_that_class(self, db, world, test_tenant):
        ann = Announcement(id=uuid.uuid4(), tenant_id=test_tenant.id, title="B only", body="x",
                           level="CLASS", class_id=world["class_b"].id, created_by=world["admin"].id)
        db.add(ann)
        await db.commit()
        svc = get_announcement_service()
        for outsider in (world["parent_a"], world["teacher"]):
            with as_user(outsider):
                with pytest.raises(NotFoundException):
                    await svc.get_announcement(db, ann.id)
                listed, _ = await svc.get_announcements(db)
                assert ann.id not in {a.id for a in listed}
        with as_user(world["parent_b"]):
            assert (await svc.get_announcement(db, ann.id)).id == ann.id


# ─────────────────────────── Exams ───────────────────────────


class TestExamScope:
    async def test_teacher_cannot_reach_other_class_exam(self, db, world, test_tenant):
        subj = Subject(id=uuid.uuid4(), tenant_id=test_tenant.id, name="Maths",
                       code=f"M-{uuid.uuid4().hex[:4]}", is_active=True)
        exam = Exam(id=uuid.uuid4(), tenant_id=test_tenant.id, class_id=world["class_b"].id,
                    name="T1", academic_year="2026", term=1)
        db.add_all([subj, exam])
        await db.flush()
        paper = ExamSubject(id=uuid.uuid4(), tenant_id=test_tenant.id, exam_id=exam.id,
                            subject_id=subj.id, total_marks=100)
        db.add(paper)
        await db.commit()

        svc = get_exam_service()
        with as_user(world["teacher"]):
            assert exam.id not in {e.id for e in await svc.list_exams(db)}
            with pytest.raises(NotFoundException):
                await svc.get_exam(db, exam.id)
            with pytest.raises(NotFoundException):
                await svc.list_results(db, paper.id)
            with pytest.raises(NotFoundException):
                await svc.save_results(db, paper.id, rows=[
                    {"student_id": str(world["child_b"].id), "marks_obtained": 1},
                ])
        with as_user(world["admin"]):
            assert (await svc.get_exam(db, exam.id)).id == exam.id
            rows = await svc.save_results(db, paper.id, rows=[
                {"student_id": str(world["child_b"].id), "marks_obtained": 70},
                {"student_id": str(world["foreign_student"].id), "marks_obtained": 70},
            ])
            assert [r.student_id for r in rows] == [world["child_b"].id]


# ─────────────────────────── Invitations ───────────────────────────


class TestInvitationScope:
    async def test_teacher_only_sees_and_creates_for_own_class(self, db, world, test_tenant):
        inv_b = ParentInvitation(
            id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=world["child_b"].id,
            email="someone@test.local", invitation_code=uuid.uuid4().hex[:8].upper(),
            status="PENDING", created_by=world["admin"].id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(inv_b)
        await db.commit()
        svc = get_invitation_service()
        with as_user(world["teacher"]):
            listed, _ = await svc.list_invitations(db)
            assert inv_b.id not in {i.id for i in listed}
            assert await svc.get_invitation(db, inv_b.id) is None
            assert await svc.resend_invitation(db, inv_b.id) is None
            with pytest.raises(Exception):
                await svc.create_invitation(db, world["child_b"].id, "hijack@test.local")
        with as_user(world["admin"]):
            assert (await svc.get_invitation(db, inv_b.id)).id == inv_b.id


# ─────────────────────────── Web / API guards ───────────────────────────


class TestGuards:
    async def test_onboarding_is_admin_only(self, world):
        from app.web.onboarding import _require_school_admin

        for outsider in (world["teacher"], world["parent_a"]):
            with as_user(outsider):
                with pytest.raises(ForbiddenException):
                    _require_school_admin()
        with as_user(world["admin"]):
            _require_school_admin()

    async def test_academic_class_guard_blocks_foreign_and_other_class(self, db, world):
        from app.api.v1.academic import _ensure_class_visible

        with as_user(world["admin"]):
            await _ensure_class_visible(db, world["class_b"].id)
            with pytest.raises(NotFoundException):
                await _ensure_class_visible(db, world["foreign_class"].id)
        with as_user(world["teacher"]):
            with pytest.raises(NotFoundException):
                await _ensure_class_visible(db, world["class_b"].id)
