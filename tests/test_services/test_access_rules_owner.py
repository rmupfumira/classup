"""Owner decisions on the access-control audit (2026-11-21).

1. MIS: teachers only get analytics for one of their own classes.
2. Events: teachers see school-wide + own-class + own events; edit only their own.
3. Placement: teachers can't put students in, or move them out of, classes they don't teach.
4. Subject teachers: subject access only (their papers), not the whole class.
5. File deletion: admins may delete others' files, always audited.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.exceptions import ForbiddenException, NotFoundException
from app.models import AttendanceRecord, FileEntity, SchoolEvent, Student, Subject
from app.models.academic import ClassSubjectTeacher
from app.models.audit import AuditLog
from app.models.exam import Exam, ExamSubject
from app.schemas.student import StudentCreate, StudentUpdate
from app.services import event_service
from app.services.exam_service import get_exam_service
from app.services.file_service import get_file_service
from app.services.mis_service import get_mis_service
from app.services.student_service import get_student_service
from tests.test_services.test_access_scope import _user, as_user, world  # noqa: F401


# ─────────────────────────── 1. MIS ───────────────────────────


class TestMisTeacherScope:
    async def test_teacher_needs_one_of_their_own_classes(self, db, world):
        from app.api.v1.mis import _ensure_teacher_class_scope

        with as_user(world["teacher"]):
            await _ensure_teacher_class_scope(db, world["class_a"].id)
            with pytest.raises(ForbiddenException):
                await _ensure_teacher_class_scope(db, None)
            with pytest.raises(ForbiddenException):
                await _ensure_teacher_class_scope(db, world["class_a"].id, "SCHOOL")
            with pytest.raises(ForbiddenException):
                await _ensure_teacher_class_scope(db, world["class_a"].id, "GRADE_LEVEL")
            with pytest.raises(NotFoundException):
                await _ensure_teacher_class_scope(db, world["class_b"].id)
        with as_user(world["admin"]):
            await _ensure_teacher_class_scope(db, None, "SCHOOL")

    async def test_chronic_absentees_respect_class_filter(self, db, world, test_tenant):
        day = date.today() - timedelta(days=3)
        for child, cls in ((world["child_a"], world["class_a"]), (world["child_b"], world["class_b"])):
            db.add(AttendanceRecord(id=uuid.uuid4(), tenant_id=test_tenant.id, student_id=child.id,
                                    class_id=cls.id, date=day, status="ABSENT",
                                    recorded_by=world["admin"].id))
        await db.commit()
        svc = get_mis_service()
        rows = await svc.chronic_absenteeism(db, day, day, class_id=world["class_a"].id)
        assert {r["student_id"] for r in rows} == {str(world["child_a"].id)}
        kpis = await svc.kpi_summary(db, from_date=day, to_date=day, class_id=world["class_a"].id)
        names = {c["student_id"] for c in kpis["at_risk"]["chronic_absentees"]}
        assert names == {str(world["child_a"].id)}


# ─────────────────────────── 2. Events ───────────────────────────


def _event(tenant_id, created_by, **kw):
    return SchoolEvent(
        id=uuid.uuid4(), tenant_id=tenant_id, created_by=created_by, title="E",
        event_type="OTHER", starts_at=datetime.now(timezone.utc) + timedelta(days=2), **kw,
    )


class TestTeacherEvents:
    async def test_visibility_and_editing(self, db, world, test_tenant):
        admin, teacher = world["admin"], world["teacher"]
        school = _event(test_tenant.id, admin.id, scope="SCHOOL")
        own_class = _event(test_tenant.id, admin.id, scope="CLASS", class_id=world["class_a"].id)
        other_class = _event(test_tenant.id, admin.id, scope="CLASS", class_id=world["class_b"].id)
        own_student = _event(test_tenant.id, admin.id, scope="STUDENT", student_id=world["child_a"].id)
        other_student = _event(test_tenant.id, admin.id, scope="STUDENT", student_id=world["child_b"].id)
        mine_elsewhere = _event(test_tenant.id, teacher.id, scope="CLASS", class_id=world["class_b"].id)
        events = [school, own_class, other_class, own_student, other_student, mine_elsewhere]
        db.add_all(events)
        await db.commit()

        listed = await event_service.list_events(db, tenant_id=test_tenant.id, teacher_id=teacher.id)
        ids = {e.id for e in listed} & {e.id for e in events}
        assert ids == {school.id, own_class.id, own_student.id, mine_elsewhere.id}
        assert not await event_service.teacher_can_see_event(db, other_class, teacher.id)
        assert not await event_service.teacher_can_see_event(db, other_student, teacher.id)
        all_ids = {e.id for e in await event_service.list_events(db, tenant_id=test_tenant.id)}
        assert {e.id for e in events} <= all_ids

        from app.api.v1.events import _get_editable_event, _get_visible_event

        with as_user(teacher):
            assert (await _get_editable_event(db, mine_elsewhere.id)).id == mine_elsewhere.id
            with pytest.raises(HTTPException) as exc:
                await _get_editable_event(db, school.id)
            assert exc.value.status_code == 403
            with pytest.raises(HTTPException) as exc:
                await _get_visible_event(db, other_class.id)
            assert exc.value.status_code == 404
        with as_user(admin):
            assert (await _get_editable_event(db, other_class.id)).id == other_class.id


# ─────────────────────────── 3. Placement ───────────────────────────


class TestTeacherPlacement:
    async def test_create_only_in_own_class(self, db, world):
        svc = get_student_service()
        with as_user(world["teacher"]):
            student, _ = await svc.create_student(
                db, StudentCreate(first_name="New", last_name="Kid", class_id=world["class_a"].id),
            )
            assert student.class_id == world["class_a"].id
            for cls in (world["class_b"].id, None):
                with pytest.raises(ForbiddenException):
                    await svc.create_student(
                        db, StudentCreate(first_name="No", last_name="Way", class_id=cls),
                    )

    async def test_update_cannot_move_out_of_own_class(self, db, world):
        svc = get_student_service()
        with as_user(world["teacher"]):
            updated = await svc.update_student(db, world["child_a"].id, StudentUpdate(notes="ok"))
            assert updated.class_id == world["class_a"].id
            updated = await svc.update_student(
                db, world["child_a"].id, StudentUpdate(class_id=world["class_a"].id),
            )
            assert updated.class_id == world["class_a"].id
            for cls in (world["class_b"].id, None):
                with pytest.raises(ForbiddenException):
                    await svc.update_student(db, world["child_a"].id, StudentUpdate(class_id=cls))
            with pytest.raises(NotFoundException):
                await svc.update_student(
                    db, world["child_b"].id, StudentUpdate(class_id=world["class_a"].id),
                )
        with as_user(world["admin"]):
            moved = await svc.update_student(
                db, world["child_a"].id, StudentUpdate(class_id=world["class_b"].id),
            )
            assert moved.class_id == world["class_b"].id

    async def test_teacher_cannot_link_parent_to_other_class_student(self, db, world):
        from app.schemas.student import LinkParentRequest

        with as_user(world["teacher"]):
            with pytest.raises(NotFoundException):
                await get_student_service().link_parent(
                    db, world["child_b"].id,
                    LinkParentRequest(parent_id=world["parent_a"].id, relationship_type="PARENT"),
                )


# ─────────────────────────── 4. Subject teachers ───────────────────────────


class TestSubjectTeacherMarking:
    async def test_marks_only_assigned_subject(self, db, world, test_tenant):
        art = Subject(id=uuid.uuid4(), tenant_id=test_tenant.id, name="Art",
                      code=f"ART-{uuid.uuid4().hex[:4]}", is_active=True)
        maths = Subject(id=uuid.uuid4(), tenant_id=test_tenant.id, name="Maths",
                        code=f"MAT-{uuid.uuid4().hex[:4]}", is_active=True)
        exam = Exam(id=uuid.uuid4(), tenant_id=test_tenant.id, class_id=world["class_b"].id,
                    name="T1", academic_year="2026", term=1)
        db.add_all([art, maths, exam])
        await db.flush()
        art_paper = ExamSubject(id=uuid.uuid4(), tenant_id=test_tenant.id, exam_id=exam.id,
                                subject_id=art.id, total_marks=100)
        maths_paper = ExamSubject(id=uuid.uuid4(), tenant_id=test_tenant.id, exam_id=exam.id,
                                  subject_id=maths.id, total_marks=100)
        db.add_all([art_paper, maths_paper, ClassSubjectTeacher(
            id=uuid.uuid4(), class_id=world["class_b"].id, subject_id=art.id,
            teacher_id=world["teacher"].id,
        )])
        await db.commit()
        svc = get_exam_service()
        row = [{"student_id": str(world["child_b"].id), "marks_obtained": 60}]

        # Mode off: the subject-teacher table is ignored entirely.
        with as_user(world["teacher"]):
            with pytest.raises(NotFoundException):
                await svc.get_exam(db, exam.id)

        test_tenant.settings = {**test_tenant.settings, "subject_teacher_enabled": True}
        await db.commit()
        with as_user(world["teacher"]):
            assert exam.id in {e.id for e in await svc.list_exams(db)}
            assert (await svc.get_exam(db, exam.id)).id == exam.id
            assert await svc.list_results(db, art_paper.id) == []
            saved = await svc.save_results(db, art_paper.id, rows=row)
            assert [r.student_id for r in saved] == [world["child_b"].id]
            with pytest.raises(NotFoundException):
                await svc.list_results(db, maths_paper.id)
            with pytest.raises(NotFoundException):
                await svc.save_results(db, maths_paper.id, rows=row)


# ─────────────────────────── 5. File deletion ───────────────────────────


class TestFileDeletion:
    def _file(self, tenant_id, uploader):
        return FileEntity(id=uuid.uuid4(), tenant_id=tenant_id, storage_path="x/y.pdf",
                          original_name=f"doc-{uuid.uuid4().hex[:4]}.pdf",
                          content_type="application/pdf", file_size=10,
                          file_category="DOCUMENT", uploaded_by=uploader.id)

    async def _audit_rows(self, db, file_id):
        return (await db.execute(
            select(AuditLog).where(AuditLog.resource_id == str(file_id))
        )).scalars().all()

    async def test_admin_deletes_others_file_with_audit(self, db, world, test_tenant):
        fe = self._file(test_tenant.id, world["teacher"])
        db.add(fe)
        await db.commit()
        with as_user(world["admin"]):
            assert await get_file_service().delete_file(db, fe.id)
        await db.commit()
        rows = await self._audit_rows(db, fe.id)
        assert len(rows) == 1
        entry = rows[0]
        assert entry.action == "files.delete_others"
        assert entry.user_id == world["admin"].id and entry.tenant_id == test_tenant.id
        assert entry.details["original_name"] == fe.original_name
        assert entry.details["uploaded_by"] == str(world["teacher"].id)
        assert entry.details["file_id"] == str(fe.id)

    async def test_own_delete_is_not_flagged_and_non_admins_stay_restricted(self, db, world, test_tenant):
        other_teacher = _user(test_tenant.id, "TEACHER", "other")
        db.add(other_teacher)
        await db.flush()
        mine = self._file(test_tenant.id, world["teacher"])
        theirs = self._file(test_tenant.id, other_teacher)
        db.add_all([mine, theirs])
        await db.commit()
        svc = get_file_service()
        with as_user(world["teacher"]):
            assert await svc.delete_file(db, mine.id)
            with pytest.raises((ForbiddenException, NotFoundException)):
                await svc.delete_file(db, theirs.id)
        await db.commit()
        assert await self._audit_rows(db, mine.id) == []
        assert (await db.get(FileEntity, theirs.id)).deleted_at is None
